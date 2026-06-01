# Replication Collection Pipeline

This folder contains the prompts, config, and helper scripts for collecting a fresh dataset for *Discriminatory Compliance: How LLMs Answer Queries from Protected Groups*. It will need some minor configuration for your local environment (including API keys in a .env) to work. 

Start with:

```powershell
python pipeline.py plan
python pipeline.py build-requests --output my_requests.jsonl
python publish/pipeline.py validate --input my_tagged_responses.jsonl --require-tags
```

Model calls and judge calls are run in your own environment.
