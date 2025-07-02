
Drop Schema and reinitialize: 
docker exec -it tsdb psql -U quantuser -d quantdata -c "DROP SCHEMA IF EXISTS earnings_data CASCADE;"
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/20_earnings_data_schema.sql
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/30_earnings_data_views.sql
docker exec -it tsdb psql -U quantuser -d quantdata -f /docker-entrypoint-initdb.d/40_clean_raw_sources_view.sql