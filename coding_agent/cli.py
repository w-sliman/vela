from __future__ import annotations
import argparse
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm,Prompt
from rich.table import Table
from .config import Config
from .llm import CodingAgent,PauseInterrupt
from .session import Session
from .shell import Shell
from .workspace import Workspace
from .tools import ToolContext
from .git import Git
from .browser import Browser
from .github import GitHub
from .sandbox import DockerSandbox
from .agents import Delegator
from .telemetry import USAGE_ADVICE
from .events import EventBus
from .ui import DebugUI,_fmt_tokens
from .resume import list_sessions,resolve_session,build_digest
from . import __version__
console=Console()
debug_ui=DebugUI(console)
def approval_callback(command,reason):
    console.print(Panel(f'[bold yellow]Approval required[/bold yellow]\n\nReason: {reason}\n\n[dim]{command}[/dim]',border_style='yellow'));return Confirm.ask('Execute?',default=False)
def make_approval_callback(mode):
    """Return the approval callback that actually implements the configured mode."""
    if mode=='auto':
        console.print('[yellow]Approval mode: auto — risky commands run without prompting.[/]')
        return lambda command,reason: True
    if mode=='deny':
        console.print('[yellow]Approval mode: deny — risky commands are rejected.[/]')
        return lambda command,reason: False
    return approval_callback
def show_banner(c):
    console.print(Panel.fit(f'[bold cyan]Workspace Coding Agent v{__version__}[/bold cyan]\n[dim]Inspect • Edit • Run • Test • Review[/dim]\n\nWorkspace: [bold]{c.workspace}[/bold]\nModel: [bold]{c.model or "not configured"}[/bold]\nEndpoint: [bold]{c.base_url or "OpenAI default"}[/bold]\nAPI: [bold]{c.api_mode}[/bold]\nApproval: [bold]{c.approval_mode}[/bold]',border_style='cyan'))
    if getattr(c, 'approval_edits', False):
        console.print('[yellow]Edit approval ON — every file change asks first[/yellow]')
def main():
    p=argparse.ArgumentParser(description='Interactive venv-native coding agent');p.add_argument('--workspace');p.add_argument('--plan',action='store_true');a=p.parse_args()
    c=Config.from_env(a.workspace);ws=Workspace(c.workspace,c.max_file_chars);shell=Shell(c);session=Session(c.workspace)
    debug_ui.enabled=c.debug
    approval=make_approval_callback(c.approval_mode)
    ctx=ToolContext(c,ws,shell,approval,Git(c.workspace),Browser(c.enable_browser,c.allow_private_urls),GitHub(c.enable_github),DockerSandbox(c.workspace,c.enable_sandbox),lambda line: console.print(f'[dim]{line.rstrip()}[/dim]'),Delegator(c, f'Workspace: {c.workspace}', EventBus(debug_ui.event)),EventBus(debug_ui.event),on_tool_result=debug_ui.tool_result);show_banner(c)
    if not c.api_key or not c.model: console.print(Panel('Configure OPENAI_API_KEY and OPENAI_MODEL in .env.',title='LLM configuration required',border_style='red'));raise SystemExit(2)
    agent=CodingAgent(c,ctx,session,EventBus(debug_ui.event))
    if agent.window_source!='configured':
        console.print(f'[dim]context window: {agent.budget.window:,} tokens ({agent.window_source})[/dim]')
    if a.plan: console.print(Markdown(agent.run('Create an implementation plan for this repository. Do not edit files.').text));return
    while True:
        try:text=Prompt.ask('[bold green]you[/bold green]').strip()
        except (EOFError,KeyboardInterrupt):console.print();break
        if not text:continue
        try:
            if text in {'/quit','/exit'}:break
            if text=='/help':
                t=Table(title='Commands');t.add_column('Command');t.add_column('Meaning')
                for x in [('/help','commands'),('/pwd','workspace'),('/tree','tree'),('/model','model/endpoint'),('/usage','session token usage'),('/compact [focus]','summarize older conversation turns'),('/undo','revert last agent edit checkpoint (auto: commits only)'),('/memory [consolidate]','persistent memory; consolidate merges duplicates'),('/todos','current working todo list'),('/sessions [n]','list recent session traces'),('/resume [id|#]','continue a past session as fresh digest context'),('/continue','resume a paused run (Ctrl+C)'),('/history','session events'),('/clear','clear LLM context'),('/quit','exit')]:t.add_row(*x)
                console.print(t);continue
            if text=='/pwd':console.print(c.workspace);continue
            if text=='/tree':console.print('\n'.join(ws.list_files()));continue
            if text=='/model':
                extra='' if agent.mode==c.api_mode else f', now {agent.mode}'
                console.print(f'{c.model} @ {c.base_url or "OpenAI default"} ({c.api_mode}{extra})')
                console.print(f'[dim]context window: {agent.budget.window:,} tokens ({agent.window_source}), '
                              f'{agent.budget.limit:,} usable after reply headroom[/dim]');continue
            if text=='/usage':
                m=agent.metrics;win=agent.budget.window;m.price(c.price_input_per_million,c.price_output_per_million)
                avg=(m.latency_ms/m.calls) if m.calls else 0.0
                console.print(f'[dim]LLM calls: {m.calls} | tokens in/out/total: {m.input_tokens}/{m.output_tokens}/{m.input_tokens+m.output_tokens} | est. cost: ${m.estimated_cost_usd:.4f} | avg latency: {avg:.0f} ms[/dim]')
                console.print(f'[dim]context: last prompt {_fmt_tokens(m.last_input_tokens)} of {_fmt_tokens(win)} window ({(m.last_input_tokens/win*100 if win else 0):.0f}%) | window {agent.window_source}[/dim]' if m.last_input_tokens else f'[dim]context: no usage reported yet (window {win:,}, {agent.window_source})[/dim]')
                if m.missing_usage:console.print(f'[yellow]{m.missing_usage} call(s) reported no usage data (excluded from totals). {USAGE_ADVICE}[/]')
                continue
            if text=='/undo':
                if not ctx.git.ensure_repo():
                    console.print('[yellow]no git repository in workspace[/]')
                elif not approval('git reset --hard <last agent checkpoint>','undo restores the workspace to the state before the last agent edit checkpoint'):
                    console.print('[dim]cancelled.[/dim]')
                else:
                    ok,msg=ctx.git.undo_last_checkpoint()
                    console.print(f'[green]{msg}[/green]' if ok else f'[red]{msg}[/red]')
                continue
            if text=='/memory' or text.startswith('/memory '):
                from .memory import ProjectMemory;pm=ProjectMemory(c.workspace)
                arg=text[len('/memory'):].strip()
                if arg.startswith('consolidate'):
                    focus=arg[len('consolidate'):].strip() or None
                    console.print('[dim]consolidating memory…[/dim]')
                    res=agent.consolidate_memory(focus)
                    if res.get('reason'):console.print(f"[yellow]{res['reason']} — nothing to do.[/yellow]")
                    else:console.print(f"[dim]memory consolidated: {res['merged']} group(s) merged, {res['removed']} record(s) removed, {res['pruned']} pruned | records {res['before']}→{res['after']}[/dim]")
                else:console.print(pm.text())
                continue
            if text=='/history':
                for e in session.recent():console.print(e)
                continue
            if text=='/todos':
                td=agent.todos
                if not td:console.print('[dim]no todos yet — the agent creates them for non-trivial tasks[/dim]')
                else:
                    for t in td:
                        mark={'done':'[green]✓[/green]','in_progress':'[cyan]›[/cyan]'}.get(str(t.get('status')),'[dim]○[/dim]')
                        console.print(f'  {mark} {t.get("text")}')
                continue
            if text=='/clear':agent.clear();console.print('[dim]LLM context cleared.[/dim]');continue
            if text=='/sessions' or text.startswith('/sessions '):
                import datetime as _dt
                arg=text[len('/sessions'):].strip()
                n=int(arg) if arg.isdigit() else 10
                rows=list_sessions(c.workspace,exclude=session.path,limit=max(1,min(n,100)))
                if not rows:console.print('[yellow]no recorded sessions yet[/yellow]')
                else:
                    t=Table(title='Recent sessions (newest first)')
                    t.add_column('#');t.add_column('id');t.add_column('UTC start');t.add_column('turns');t.add_column('first request')
                    for i,s in enumerate(rows,1):
                        when=_dt.datetime.fromtimestamp(s['mtime'],_dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
                        t.add_row(str(i),s['id'],when,str(s['turns']),s['first_user'][:60] or '—')
                    console.print(t);console.print('[dim]/resume continues the newest; /resume <#|id-prefix> picks another.[/dim]')
                continue
            if text=='/resume' or text.startswith('/resume '):
                found,err=resolve_session(c.workspace,text[len('/resume'):].strip() or None,exclude=session.path)
                if err:console.print(f'[yellow]{err}[/yellow]');continue
                d=build_digest(found['path'],max_chars=c.resume_max_chars)
                agent.start_resumed(d['text'],found['id'])
                sid=found['id'];nfiles=len(d['files'])
                console.print(f'[green]resumed [bold]{sid}[/bold][/green] — {d["requests"]} request(s), {nfiles} file(s) touched; context rebuilt as digest.')
                console.print('[dim]prior files may have changed since — verify before editing.[/dim]')
                continue
            if text=='/compact' or text.startswith('/compact '):
                focus=text[len('/compact'):].strip() or None
                console.print('[dim]compacting…[/dim]')
                res=agent.compact(focus)
                if not res.get('compacted',True):console.print(f"[yellow]{res['reason']}[/]")
                else:
                    console.print(f"[dim]compacted: {res['turns_removed']} turn(s) summarized, kept last {res['turns_kept']} | history {res['items_before']}→{res['items_after']} items[/dim]")
                    console.print(Markdown(res['summary']))
                continue
            if text=='/continue':
                if not agent.history:console.print('[yellow]nothing to continue — context is empty[/yellow]');continue
                console.print('[bold magenta]agent[/bold magenta] continuing…')
                r=agent.resume()
                if not r.streamed:console.print(Markdown(r.text))
                continue
            console.print('[bold magenta]agent[/bold magenta] thinking…')
            r=agent.run(text)
            if not r.streamed:console.print(Markdown(r.text))
            m=agent.metrics;win=agent.budget.window;ctxpct=(m.last_input_tokens/win*100) if win and m.last_input_tokens else 0.0
            console.print(f'[dim]tool calls: {r.tool_calls} | latency: {r.metrics["latency_ms"]:.0f} ms | session: {session.path.name}[/dim]')
            mids=agent.last_memory_ids
            if mids:console.print(f'[dim]memory: {", ".join(mids)}[/dim]')
            td=agent.todos
            if td:
                done=sum(1 for t in td if str(t.get('status'))=='done')
                console.print(f'[dim]todos: {done}/{len(td)} done[/dim]')
            console.print(f'[dim]tokens: {m.input_tokens} in / {m.output_tokens} out / {m.input_tokens+m.output_tokens} total | context: {_fmt_tokens(m.last_input_tokens)}/{_fmt_tokens(win)} ({ctxpct:.0f}%)[/dim]' if m.last_input_tokens else f'[dim]tool calls: {r.tool_calls} (no usage data reported by endpoint)[/dim]')
        except PauseInterrupt:
            console.print('[yellow]paused — plan, todos and history kept; type /continue to resume[/yellow]');continue
        except KeyboardInterrupt:
            console.print('\n[dim]interrupted[/dim]');continue
        except Exception as e:
            console.print(Panel(f'{type(e).__name__}: {e}',title='Error',border_style='red'))
if __name__=='__main__':main()
