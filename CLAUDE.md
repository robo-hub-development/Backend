# robo-hub

Backend stack: Python, Django, Django REST Framework, PostgreSQL (Celery/Redis only when justified). Product requirements: `docs/Stem Robo Hub PRD.pdf`.

## Engineering guidance
- Always-on rules: @.claude/rules/backend-engineering.md
- For implementing, reviewing, or optimizing backend work, use the `backend-engineer` skill (`.claude/skills/backend-engineer/SKILL.md`).
- Read the PRD before inventing any business rule; ask if it is silent or ambiguous.
