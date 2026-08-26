from __future__ import annotations
from .providers import OpenAICompatibleProvider
class Delegator:
    def __init__(self,config,workspace_context=''):
        self.provider=OpenAICompatibleProvider(config.api_key,config.base_url);self.model=config.model;self.context=workspace_context
    def run(self,role,task):
        role_prompt={
            'planner':'You are a senior software architect. Produce a concise, actionable implementation plan. Do not edit files.',
            'reviewer':'You are a strict code reviewer. Identify correctness, security, testing, and maintainability issues. Do not edit files.',
        }.get(role)
        if not role_prompt: raise ValueError('role must be planner or reviewer')
        r=self.provider.chat(model=self.model,messages=[{'role':'system','content':role_prompt},{'role':'user','content':f'Workspace context:\n{self.context}\n\nTask:\n{task}'}])
        return r.choices[0].message.content or ''
