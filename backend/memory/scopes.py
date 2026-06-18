def group_id(namespace, project=None, agent=None, session=None):
    parts = [namespace]
    if project:
        parts.append(project)
    if agent:
        parts += ["agent", agent]
    if session:
        parts += ["session", session]
    return "__".join(parts)
