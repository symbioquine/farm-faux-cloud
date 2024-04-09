import asyncio
import json
import logging
import os
import socket
import subprocess
import uuid
from textwrap import dedent

import docker
from aiohttp import web

WHITELISTED_MODULES = (os.environ.get('DRUPAL_MODULE_WHITELIST') or '').split(',')

def execute(cmd, *args, **kwargs):
    return subprocess.check_output(cmd.format(*args, **kwargs), stderr=subprocess.STDOUT, shell=True)

routes = web.RouteTableDef()

class Tenet(object):
    def __init__(self, farm_id, container):
        self.farm_id = farm_id
        self.container = container
        self.ready_event = asyncio.Event()

    def mark_ready(self):
        self.ready_event.set()

    def wait_ready(self):
        return self.ready_event.wait()

    def dispose(self):
        cfg = os.path.join("./tenets_nginx_conf.d", "{farm_id}.conf".format(farm_id=self.farm_id))

        os.remove(cfg)

        self.container.stop()
        self.container.wait(condition='not-running')
        self.container.remove(v=True)


@routes.post('/meta/farm')
async def create_farm_handler(request):
    farm_id = str(uuid.uuid4())[:8]
    admin_password = str(uuid.uuid4())[:8]

    farm_container_name = "farm-faux-cloud-tenet-{farm_id}".format(farm_id=farm_id)

    additional_module_installation_block = ''
    if 'with-module' in request.query:
      additional_modules = request.query.getall('with-module')

      # Validate all additional modules are in the whitelist environment variable
      for module_name in additional_modules:
        if module_name not in WHITELISTED_MODULES:
          response = {
            'id': farm_id,
            'message': 'Module {module_name!r} is not in whitelist'.format(module_name=module_name),
          }

          return web.Response(status=400, text=json.dumps(response), content_type="application/json")

      additional_module_installation_block = "\n".join(
        [dedent('''
        composer require drupal/{module_name}
        drush en {module_name}
        '''.format(module_name=module_name)) for module_name in additional_modules]
      )

    init_command = dedent('''
        set -ex

        chown -R www-data:www-data /opt/drupal

        ln -s /opt/drupal/web /opt/drupal/web/{farm_id}

        echo "<?php

          \$sites = [
            'farmos.test.{farm_id}' => 'default',
            'localhost.{farm_id}' => 'default',
          ];
        " >> /opt/drupal/web/sites/sites.php

        drush config-set -y system.site name 'Farm {farm_id}'
        drush upwd admin '{admin_password}'

        {additional_module_installation_block}

        curl -s -X POST 'http://{self_container_id}/meta/farm/{farm_id}/ready'

        exec docker-entrypoint.sh apache2-foreground
    '''.format(
        farm_id=farm_id,
        admin_password=admin_password,
        self_container_id=request.app['self_container_id'],
        additional_module_installation_block=additional_module_installation_block,
    ))

    farm_sites_volume_name = "farm-faux-cloud-tenet-vol-sites-{farm_id}".format(farm_id=farm_id)
    farm_keys_volume_name = "farm-faux-cloud-tenet-vol-keys-{farm_id}".format(farm_id=farm_id)

    docker_client = request.app['docker_client']

    sites_volume = docker_client.volumes.create(name=farm_sites_volume_name)
    keys_volume = docker_client.volumes.create(name=farm_keys_volume_name)

    base_img_tag = request.app['base_img_tag']

    # TODO: Figure out how to make this play nicer with asyncio (i.e. await instead of blocking however breifly)
    container = docker_client.containers.run(
        base_img_tag,
        entrypoint="/bin/bash",
        command=["-c", init_command],
        detach=True,
        auto_remove=False,
        name=farm_container_name,
        network=request.app['cloud_network'].name,
        volumes={
          farm_sites_volume_name: {'bind': '/opt/drupal/web/sites'},
          farm_keys_volume_name: {'bind': '/opt/drupal/keys'},
        }
    )

    tenet = Tenet(farm_id, container)

    request.app['tenets'][farm_id] = tenet

    try:
      await asyncio.wait_for(tenet.wait_ready(), timeout=30)
    except asyncio.TimeoutError:
      response = {
        'id': farm_id,
        'message': 'Timeout creating farm instance',
        'logs': container.logs().decode("utf-8"),
      }

      return web.Response(status=500, text=json.dumps(response), content_type="application/json")

    cfg = os.path.join("./tenets_nginx_conf.d", "{farm_id}.conf".format(farm_id=farm_id))

    with open(cfg, 'w') as fp:
        fp.write(dedent('''
    location /{farm_id} {{
      proxy_pass                            http://{farm_container_name}:80;

      proxy_set_header Host                 $host;
      proxy_set_header X-Real-IP            $remote_addr;
      proxy_set_header X-Forwarded-For      $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto    $scheme;
      proxy_set_header X-Forwarded-Protocol $scheme;
      proxy_set_header X-Forwarded-Host     $http_host;
    }}
        '''.format(
            farm_id=farm_id,
            farm_container_name=farm_container_name,
        )))

    execute("nginx -s reload")

    response = {
        'id': farm_id,
        'path': "/{farm_id}".format(farm_id=farm_id),
        'username': 'admin',
        'password': admin_password,
    }

    return web.Response(status=201, text=json.dumps(response), content_type="application/json")

# Internal endpoint used by container to inform us that it is (just about) ready to handle traffic
@routes.post('/meta/farm/{farm_id}/ready')
async def post_farm_ready_handler(request):
    farm_id = request.match_info['farm_id']
    tenets = request.app['tenets']

    # TODO: Return 400 if tenet does not exist
    tenets[farm_id].mark_ready()

    response = {
        'id': farm_id,
        'ready': True,
    }

    return web.Response(status=200, text=json.dumps(response), content_type="application/json")

@routes.delete('/meta/farm/{farm_id}')
async def delete_farm_handler(request):
    farm_id = request.match_info['farm_id']
    tenets = request.app['tenets']

    # TODO: Return 400 if tenet does not exist
    tenet = request.app['tenets'][farm_id]

    # TODO: Figure out how to make this play nicer with asyncio (i.e. await instead of blocking however breifly)
    request.app['cloud_network'].disconnect(tenet.container)
    tenet.dispose()

    del request.app['tenets'][farm_id]

    response = {
        'id': farm_id,
        'deleted': True,
    }

    return web.Response(status=200, text=json.dumps(response), content_type="application/json")

def main():
    logging.basicConfig(level=logging.DEBUG)

    docker_client = docker.from_env()

    self_container_id = socket.gethostname()

    # Create a base image so a common installation data can get reused for all tenets
    base_img_tag = "{self_container_id}-base-img".format(self_container_id=self_container_id)
    with open('/app/tenet_dockerfile', 'rb') as tenet_dockerfile:
        img = docker_client.images.build(
            fileobj=tenet_dockerfile,
            tag=base_img_tag,
        )

    # Create a private network for ourself and the tenets
    cloud_network_name = "farm-faux-cloud-" + self_container_id
    cloud_network = docker_client.networks.create(cloud_network_name, driver="bridge")

    # Add ourself to the network
    cloud_network.connect(self_container_id)

    app = web.Application()
    app.add_routes(routes)

    # TODO: Consider some mechanism to avoid enough of this state that multiple meta processes could be used
    app['docker_client'] = docker_client
    app['cloud_network'] = cloud_network
    app['self_container_id'] = self_container_id
    app['base_img_tag'] = base_img_tag
    app['tenets'] = {}

    logging.info("Meta server almost ready...")

    try:
        web.run_app(app)
    finally:
        # Remove ourself from the network
        cloud_network.disconnect(self_container_id)

        for farm_id, tenet in list(app['tenets'].items()):
            cloud_network.disconnect(tenet.container)
            tenet.dispose()
            del app['tenets'][farm_id]

        # Finally remove our network
        cloud_network.remove()

if __name__ == '__main__':
    main()
