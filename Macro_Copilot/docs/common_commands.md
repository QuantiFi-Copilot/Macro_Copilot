Historical Playbook running: 
1. Run one playbook: 
python utils/historical_extractor.py --playbook bond_futures
or: 
python utils/historical_extractor.py --playbook bond_futures.yml

2. Run multiple playbooks: 
python utils/historical_extractor.py --playbook bond_futures --playbook sovereign_bonds --playbook ois
or 
python utils/historical_extractor.py --playbook bond_futures,sovereign_bonds,ois

3. Run all playbooks: 
python utils/historical_extractor.py

docker exec -it macro-tsdb psql -U quantuser -d macrodata

To run a script from in the container: 

docker compose exec rates-agent-dev bash
micromamba run -n macro-env python -m tests.test for e.g 
