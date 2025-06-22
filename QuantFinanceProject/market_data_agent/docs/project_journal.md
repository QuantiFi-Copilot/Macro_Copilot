Work Done: 






Work left to be done: 
- Improve latency by changing the refresh token to a cloud based job instead of calling it each time 
- Change the universe from the universe.py to a database schema with each stock's metadata tagged with it. 
- Set up Slack alerts for failure 
- Set up the refresh token to make it a Prefect job at 6am and detach it from the ingestion scripts 


Command to start database: 
- docker start timescaledb

Command to start Prefect: 
1) prefect worker start -q default
2) prefect server start 

Prefect Cloud API: 
- pnu_XNI17PBqdQR189cQhQLr5tDXOmGZzN2xzwY3
- email account: sreeramandra8989@gmail.com

Prefect.yaml deploy command: 
docker compose exec prefect-worker micromamba run -n quant-env prefect deploy --all

Prefect check logs: 
docker compose logs -f prefect-worker


