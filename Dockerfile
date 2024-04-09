FROM python:3.10-slim-bullseye

LABEL maintainer="Symbioquine <symbioquine@gmail.com"

RUN apt-get update && apt-get install build-essential libffi-dev libssl-dev nginx supervisor -y \
    && pip install poetry \
    && rm -rf /var/lib/apt/lists/*

RUN rm /etc/nginx/sites-available/default 

COPY ./pyproject.toml /app/
WORKDIR /app
RUN poetry install --no-root --only main
COPY . /app
RUN poetry install --no-root --only main

# By default, Nginx will run a single worker process, setting it to auto
# will create a worker for each CPU core
ENV NGINX_WORKER_PROCESSES 1

# Custom Supervisord config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

RUN chmod +x /app/start.sh

# Copy the entrypoint that will generate Nginx additional configs
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

WORKDIR /app
CMD ["/app/start.sh"]
