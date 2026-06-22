# mini_swe_agent

A minimal [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) harness whose
entire behavior lives in one manifest (`mini_swe_agent.yaml`): the `agent`/`environment`/`model`
sections, with the prompts inlined as Jinja templates and a single implicit tool — bash.

## Local test

```bash
pip install mini-swe-agent
export ANTHROPIC_API_KEY=...        # or the key for whatever model_name is set
python3 run.py
```

Or via the upstream CLI (this directory's manifest passed with `-c`):

```bash
mini -c mini_swe_agent.yaml
```
