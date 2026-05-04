# FX playbooks

This directory is the canonical home for FX ingestion playbooks.

The FX agent keeps analytics code under subdomains such as `spot/`,
`forwards/`, `vol/`, and `macro/`, but all FX playbooks live here to match the
top-level `rates_agent/playbooks/` convention.

`prompt_playbook.md` is the operator-facing companion: it lists prompts that
exercise the FX tools coherently during research, testing, and demos.
