Drop Schema and reinitialize: 
docker exec -it tsdb psql -U quantuser -d quantdata -c "DROP SCHEMA IF EXISTS earnings_data CASCADE;"
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/20_earnings_data_schema.sql
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/30_earnings_data_views.sql
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/40_clean_parsed_view.sql

docker command to create a temp service to go inside the container and run a script: 
docker run --rm -it --network quantfinanceproject_default -v "$PWD":/app -w /app --env-file .env quantfinanceproject:latest micromamba run -n quant-env bash

then: micromamba run -n quant-env python -m earnings_agent.script

exec into DB: docker exec -it tsdb psql -U quantuser -d quantdata

aws sso login --profile AdministratorAccess-172982781876
