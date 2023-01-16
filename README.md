# farm-faux-cloud

A minimal "fake cloud" of farmOS container instances for testing purposes.

***Warning: This implementation makes no effort to preserve any data in the managed farmOS containers or otherwise protect you from data loss therein. Please exercise caution and probably only use this for ephemeral test data.***

## What does this do?

Provides a Docker container which has an HTTP api for spinning up and destroying multiple farmOS container instances as subpaths under the same reverse proxy as the HTTP api.

## Okay, but I still don't get it...

We can run the faux cloud via:

```sh
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock farm-faux-cloud:latest
```

Then create farmOS instances with an HTTP POST request:

```sh
curl -i -X POST "http://localhost/meta/farm"
# Returns: {"id": "8df47809", "path": "/8df47809", "username": "admin", "password": "57ab8e1f"}
```

Then access the new farmOS instance at "http://localhost/8df47809" with the provided username/password.

When we're done the instance can be destroyed via the same api:

```sh
curl -i -X DELETE "http://localhost/meta/farm/8df47809"
# Returns: {"id": "8df47809", "deleted": true}
```

Stopping the farm-faux-cloud docker container will also destroy all the currently running farmOS instances it is managing.

## Why is that useful, can't I just create/destroy farmOS instances directly with Docker?

That's true, but this tool provides a place to encapsulate the logic for spinning up and destroying farmOS instances so it can be reused between the tests for various clients/integrations of farmOS
more easily.

As an example, integration tests for Field Kit could use the http API to spin up fresh instances for each suite of tests that create/modify server-side data without worrying about tests interfering
with eachother.

farm-faux-cloud could also serve as a sandbox for building more sophisticated "farm cloud" features that might eventually be leveraged in non-testing use-cases. i.e. managed backups, self-service farmOS
instance creation, usage-based billing, etc.

## Building

```sh
docker build -t farm-faux-cloud .
```

## Run

```sh
docker-compose up -d
```

## Credit

* farmOS: https://farmOS.org/
* aiohttp + NGINX dockerized configuration originally based on https://pizzaandcode.com/posts/aiohttp_nginx_gunicorn/ https://github.com/riccardolorenzon/aiohttp-gunicorn-nginx