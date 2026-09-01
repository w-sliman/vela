from __future__ import annotations
from .providers import OpenAICompatibleProvider,backoff_delays,with_retries

ROLES={
    'planner':'You are a senior software architect. Produce a concise, actionable implementation plan. Do not edit files.',
    'reviewer':'You are a strict code reviewer. Identify correctness, security, testing, and maintainability issues. Do not edit files.',
}

class Delegator:
    """Isolated planner/reviewer sub-agent: advisory only, no tools, no file access.

    The client is built on first use rather than at construction, so a REPL that
    never delegates never opens one. Calls go through the same backoff as the main
    loop — a sub-agent is no less exposed to a transient provider failure, and this
    was the one model call in the codebase without it.
    """
    def __init__(self,config,workspace_context='',events=None):
        self.config=config;self.model=config.model;self.context=workspace_context
        self.events=events;self._provider=None
    @property
    def provider(self):
        if self._provider is None:
            self._provider=OpenAICompatibleProvider(self.config.api_key,self.config.base_url)
        return self._provider
    @provider.setter
    def provider(self,value):self._provider=value
    def _emit(self,message):
        if self.events:self.events.emit('info',message)
    def run(self,role,task):
        role_prompt=ROLES.get(role)
        if not role_prompt: raise ValueError(f"role must be one of: {', '.join(sorted(ROLES))}")
        messages=[{'role':'system','content':role_prompt},
                  {'role':'user','content':f'Workspace context:\n{self.context}\n\nTask:\n{task}'}]
        r=with_retries(lambda:self.provider.chat(model=self.model,messages=messages),
                       backoff_delays(getattr(self.config,'request_retries',2)),
                       on_wait=lambda d,i:self._emit(f'delegate({role}): retrying in {d:.1f}s'),
                       on_failure=lambda exc,i:self._emit(
                           f'delegate({role}) attempt {i+1} failed: {type(exc).__name__}: {str(exc)[:120]}'))
        return r.choices[0].message.content or ''
