1. Problem 1: 

What I would do later

When you are ready for v2 ingestion efficiency, use a hybrid refresh model:

Daily normal run
	•	ingest only a recent rolling window, for example:
	•	last 30 days
	•	or last 90 days

Periodic deep refresh
	•	run a full-history refresh less often:
	•	weekly
	•	monthly
	•	or manually

That gives you:
	•	fresh new daily points
	•	some protection against recent revisions

2. Problem 2: 

Incremental ingestion pre-delete sanity gate
	•	validate row count / coverage / expected instrument count before deleting window - for the incremental extractor script + incremental ingestion. 