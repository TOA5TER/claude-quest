# Shared by the hub and client tests: both sides duplicate the story-name rule,
# so one table keeps them in step.

# (name, story_id, role, repo, label)
GROUPED_NAMES = [
    ("sc-1000-orchestrator", "sc-1000", "orchestrator", "", "orchestrator"),
    ("sc-1234-developer-api", "sc-1234", "developer", "api", "developer · api"),
    ("sc-1234-developer-my-web-app", "sc-1234", "developer", "my-web-app",
     "developer · my-web-app"),
    ("sc-1000--x", "sc-1000", "", "x", "-x"),
    ("SC-1000-x", "SC-1000", "x", "", "x"),
    ("sc-1000-developer-2", "sc-1000", "developer", "2", "developer · 2"),
]

UNGROUPED_NAMES = [
    "",
    "orchestrator",
    "my feature work",
    "sc-1000",
    "sc-1000-",
    "sc-abc-developer",
    "sc-1000-developer\n",
    "sc_1-x",
    "my-feature-12-x",
    "1000-developer",
    "#1000-developer",
    "PROJ2-100-developer",
]
