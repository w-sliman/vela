from __future__ import annotations
class OpenAICompatibleProvider:
    def __init__(self,api_key,base_url=None):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError('openai package is required. Run pip install -r requirements.txt') from e
        self.client=OpenAI(api_key=api_key,base_url=base_url)
    def responses(self,**kw): return self.client.responses.create(**kw)
    def chat(self,**kw): return self.client.chat.completions.create(**kw)
    def chat_stream(self,**kw):
        """Streaming chat completion; returns the stream object (create happens eagerly)."""
        kw.update(stream=True,stream_options={'include_usage':True})
        return self.client.chat.completions.create(**kw)
