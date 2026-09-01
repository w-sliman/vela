from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

@dataclass(frozen=True)
class Config:
    api_key: str|None; base_url: str|None; model: str|None; api_mode: str
    workspace: Path; approval_mode: str; max_tool_output:int; max_file_chars:int
    command_timeout:int; enable_browser:bool; enable_github:bool; enable_sandbox:bool
    telemetry:bool; debug:bool
    price_input_per_million:float=0.0; price_output_per_million:float=0.0; context_window_tokens:int=128000
    request_retries:int=2; auto_compact:bool=True; compact_keep_turns:int=3
    reply_reserve_tokens:int=0        # 0 -> ContextBudget picks window//8
    context_window_explicit:bool=False  # set by hand -> skip probing (a rejection still wins)
    stream_chat:bool=True; auto_checkpoint:bool=True
    memory_inject:bool=True; memory_top_k:int=4; memory_max_chars:int=1500; memory_min_score:float=0.5
    memory_distill:bool=True
    resume_max_chars:int=6000
    memory_max_records:int=200; memory_ttl_days:int=0
    show_todos:bool=True
    verify_gate:bool=True
    approval_edits:bool=False
    allow_private_urls:bool=False
    @classmethod
    def from_env(cls, workspace_arg=None):
        load_dotenv()
        ws=Path(workspace_arg or os.getenv('CODER_WORKSPACE','./workspace')).expanduser().resolve()
        approval=os.getenv('CODER_APPROVAL_MODE','prompt').lower()
        if approval not in {'prompt','deny','auto'}: raise ValueError('CODER_APPROVAL_MODE must be prompt, deny, or auto')
        mode=os.getenv('OPENAI_API_MODE','auto').lower()
        if mode not in {'auto','responses','chat'}: raise ValueError('OPENAI_API_MODE must be auto, responses, or chat')
        b=lambda k: os.getenv(k,'0')=='1'
        return cls(os.getenv('OPENAI_API_KEY'),os.getenv('OPENAI_BASE_URL') or None,os.getenv('OPENAI_MODEL'),mode,ws,approval,
          int(os.getenv('CODER_MAX_TOOL_OUTPUT','12000')),int(os.getenv('CODER_MAX_FILE_CHARS','30000')),int(os.getenv('CODER_COMMAND_TIMEOUT','45')),
          b('CODER_ENABLE_BROWSER'),b('CODER_ENABLE_GITHUB'),b('CODER_ENABLE_SANDBOX'),
          os.getenv('CODER_TELEMETRY','1')!='0',b('CODER_DEBUG'),
          float(os.getenv('CODER_INPUT_PRICE_PER_MILLION','0')),float(os.getenv('CODER_OUTPUT_PRICE_PER_MILLION','0')),
          int(os.getenv('CODER_CONTEXT_WINDOW','128000')),
          int(os.getenv('CODER_REQUEST_RETRIES','2')),os.getenv('CODER_AUTO_COMPACT','1')!='0',
          max(1,int(os.getenv('CODER_COMPACT_KEEP_TURNS','3'))),
          int(os.getenv('CODER_REPLY_RESERVE_TOKENS','0')),
          bool(os.getenv('CODER_CONTEXT_WINDOW')),
          os.getenv('CODER_STREAM','1')!='0',os.getenv('CODER_AUTO_CHECKPOINT','1')!='0',
          os.getenv('CODER_MEMORY_INJECT','1')!='0',int(os.getenv('CODER_MEMORY_TOPK','4')),int(os.getenv('CODER_MEMORY_MAX_CHARS','1500')),float(os.getenv('CODER_MEMORY_MIN_SCORE','0.5')),
          os.getenv('CODER_MEMORY_DISTILL','1')!='0',
          int(os.getenv('CODER_RESUME_MAX_CHARS','6000')),
          int(os.getenv('CODER_MEMORY_MAX_RECORDS','200')),int(os.getenv('CODER_MEMORY_TTL_DAYS','0')),
          os.getenv('CODER_TODOS','1')!='0',os.getenv('CODER_VERIFY_GATE','1')!='0',
          os.getenv('CODER_APPROVAL_EDITS','0')!='0',b('CODER_ALLOW_PRIVATE_URLS'))
