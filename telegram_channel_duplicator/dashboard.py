"""Multi-user control panel (stdlib HTTP server, zero front-end dependencies).

Every signed-in account AND every anonymous browser gets an isolated workspace
(config, SQLite state, Telegram session, bot thread).  The page is fully
self-contained: inline CSS/JS, no CDN, works in a sandboxed iframe.
"""

from __future__ import annotations

import json
import os
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from loguru import logger

from .auth import AuthError, Identity, new_guest_id, valid_guest_id
from .config_controller import ConfigController, ConfigError
from .workspaces import WorkspaceManager

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Duplicator · Control Panel</title>
<style>
:root{
  --bg:#080d1c; --bg2:#101a38; --panel:rgba(20,29,54,.82); --panel2:rgba(31,43,76,.7);
  --line:rgba(132,158,231,.16); --txt:#eaf0ff; --muted:#8f9ec4;
  --accent:#6c8cff; --accent2:#a06bff; --cyan:#45d6f5;
  --ok:#3ddc97; --warn:#ffc857; --err:#ff6b81; --shadow:0 18px 45px rgba(0,0,0,.35);
}
body.light{
  --bg:#eef2ff; --bg2:#dfe7ff; --panel:rgba(255,255,255,.9); --panel2:rgba(240,244,255,.9);
  --line:rgba(45,66,130,.14); --txt:#16203d; --muted:#5f6d91;
  --accent:#4a67e8; --accent2:#8a4ce0; --cyan:#0f9fc4;
  --ok:#14855a; --warn:#a9740a; --err:#c8384f; --shadow:0 14px 34px rgba(42,60,110,.16);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;min-height:100vh;color:var(--txt);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  background:radial-gradient(1100px 620px at 8% -12%,var(--bg2) 0%,var(--bg) 58%) fixed;
  transition:background-color .45s ease,color .45s ease;padding:26px}
body,section,.card,button,input,textarea,pre,.pill,.tag,.ev,.modal{transition:background-color .4s ease,color .4s ease,border-color .4s ease,box-shadow .3s ease,transform .25s ease,opacity .3s ease}
.wrap{max-width:1200px;margin:0 auto;position:relative}
.view{transition:opacity .4s cubic-bezier(.22,1,.36,1),transform .4s cubic-bezier(.22,1,.36,1)}
.view.hidden{opacity:0;transform:translateY(16px);pointer-events:none;position:absolute;left:0;right:0;top:0}
h1{font-size:23px;margin:0;letter-spacing:-.02em;font-weight:760}
h2{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);margin:0 0 15px;font-weight:700}
.sub{color:var(--muted);font-size:13px;margin-top:3px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:grid;place-items:center;font-weight:800;color:#fff;box-shadow:0 8px 22px rgba(108,140,255,.35);font-size:18px}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:7px;padding:6px 13px;border-radius:999px;font-size:12px;font-weight:700;
  background:var(--panel2);border:1px solid var(--line);white-space:nowrap}
.pill.running{color:var(--ok);border-color:rgba(61,220,151,.4)}
.pill.error{color:var(--err);border-color:rgba(255,107,129,.4)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;display:inline-block}
.running .dot{animation:pulse 1.7s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 currentColor}50%{opacity:.55;box-shadow:0 0 0 5px transparent}}
button{cursor:pointer;border:1px solid var(--line);background:var(--panel2);color:var(--txt);padding:9px 15px;
  border-radius:10px;font-weight:650;font-size:13px;transition:transform .2s ease,box-shadow .2s ease,background-color .3s,border-color .3s,color .3s}
button:hover:not(:disabled){transform:translateY(-2px);border-color:var(--accent);box-shadow:0 8px 20px rgba(80,110,220,.22)}
button:active:not(:disabled){transform:translateY(0) scale(.98)}
button:disabled{opacity:.45;cursor:not-allowed}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border-color:transparent;color:#fff;
  box-shadow:0 8px 22px rgba(108,140,255,.28)}
button.danger{color:var(--err)}
button.ghost{background:transparent}
button.small{padding:6px 11px;font-size:12px}
.check{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);border:1px solid var(--line);background:var(--panel2);padding:9px 13px;border-radius:10px;cursor:pointer}
.check input{width:auto;margin:0;accent-color:var(--accent)}
.iconbtn{width:36px;height:36px;display:grid;place-items:center;padding:0;border-radius:10px;font-size:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:20px}
.card,section{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:17px;backdrop-filter:blur(14px);box-shadow:var(--shadow)}
.card:hover{transform:translateY(-3px);border-color:rgba(108,140,255,.42)}
.card .n{font-size:30px;font-weight:780;letter-spacing:-.03em;line-height:1.1;
  background:linear-gradient(120deg,var(--txt),var(--accent));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.card .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.09em;margin-top:5px;font-weight:650}
.cols{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}
@media(max-width:900px){.cols{grid-template-columns:1fr}body{padding:16px}}
section{margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:650;font-size:10px;text-transform:uppercase;letter-spacing:.08em}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.tag{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:2px 7px;margin:2px 4px 2px 0;font-size:11px}
.muted{color:var(--muted);font-size:12px}
.events{max-height:340px;overflow:auto}
.ev{display:grid;grid-template-columns:70px 80px 1fr;gap:8px;padding:7px 2px;border-bottom:1px solid var(--line);font-size:12.5px;
  animation:fadein .45s ease both}
@keyframes fadein{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.ev .t{color:var(--muted)} .ev .k{font-weight:700;color:var(--accent)}
.ev.ERROR .k{color:var(--err)} .ev.WARNING .k{color:var(--warn)} .ev.SUCCESS .k{color:var(--ok)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px;max-height:150px;overflow:auto;color:var(--muted);font-size:11.5px;margin:0 0 12px}
textarea,input{width:100%;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
  font-family:ui-monospace,monospace;font-size:12px}
textarea{min-height:150px;resize:vertical;line-height:1.5}
input{font-family:inherit;font-size:14px;margin-bottom:11px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.msg{font-size:12.5px;min-height:18px}
.err{color:var(--err)} .ok{color:var(--ok)} .warn{color:var(--warn)}
.spin{display:inline-block;width:11px;height:11px;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.banner{background:linear-gradient(90deg,rgba(255,200,87,.13),rgba(160,107,255,.1));border:1px solid rgba(255,200,87,.3);
  border-radius:12px;padding:11px 15px;margin-bottom:18px;font-size:13px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.overlay{position:fixed;inset:0;background:rgba(4,8,20,.68);backdrop-filter:blur(6px);display:grid;place-items:center;z-index:50;
  opacity:0;pointer-events:none;transition:opacity .35s ease;padding:20px}
.overlay.show{opacity:1;pointer-events:auto}
.modal{width:min(420px,100%);background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:26px;
  box-shadow:var(--shadow);transform:translateY(22px) scale(.97);transition:transform .4s cubic-bezier(.22,1,.36,1);backdrop-filter:blur(20px)}
.overlay.show .modal{transform:none}
.modal h3{margin:0 0 4px;font-size:20px} .modal p{color:var(--muted);font-size:13px;margin:0 0 18px}
.tabs{display:flex;gap:6px;background:var(--bg);border-radius:10px;padding:4px;margin-bottom:16px}
.tabs button{flex:1;border:0;background:transparent;padding:8px;font-size:13px}
.tabs button.on{background:var(--accent);color:#fff;box-shadow:0 4px 12px rgba(108,140,255,.3)}
#toast{position:fixed;right:22px;bottom:22px;z-index:60;display:flex;flex-direction:column;gap:9px;align-items:flex-end}
.toast{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;padding:11px 16px;
  box-shadow:var(--shadow);font-size:13px;animation:slidein .35s cubic-bezier(.22,1,.36,1) both;max-width:330px}
.toast.err{border-left-color:var(--err)} .toast.ok{border-left-color:var(--ok)}
@keyframes slidein{from{opacity:0;transform:translateX(30px)}to{opacity:1;transform:none}}
footer{text-align:center;color:var(--muted);font-size:11.5px;margin:24px 0 6px}
@media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;transition-duration:.01ms!important}}
</style>
</head>
<body>
<div class="wrap">
  <div id="gate" class="view hidden" style="text-align:center;padding:80px 20px">
    <div class="logo" style="margin:0 auto 18px;width:56px;height:56px;font-size:24px">D</div>
    <h1>Sign in to your workspace</h1>
    <p class="sub">Each account has a private config, database and Telegram session.</p>
    <button class="primary" style="margin-top:20px" onclick="openAuth()">Sign in / Create account</button>
  </div>

  <div id="app" class="view">
    <header>
      <div class="brand"><div class="logo">D</div><div><h1>Channel Duplicator</h1><div class="sub" id="sub">Connecting…</div></div></div>
      <div class="spacer"></div>
      <span class="pill" id="state">—</span>
      <span class="pill" id="mode">—</span>
      <span class="pill mono" id="who">…</span>
      <button class="iconbtn ghost" id="theme" title="Toggle theme" onclick="toggleTheme()">☾</button>
      <button class="small" id="authBtn" onclick="openAuth()">Account</button>
      <button class="small ghost" id="logoutBtn" style="display:none" onclick="logout()">Sign out</button>
    </header>

    <div class="banner" id="guestBanner" style="display:none">
      <span>👋 You're in a <b>private guest workspace</b>. Nobody else can see it. Create an account to keep it across devices.</span>
      <div class="spacer"></div><button class="small primary" onclick="openAuth('register')">Create account</button>
    </div>

    <div class="row" style="margin-bottom:20px">
      <label class="check"><input type="checkbox" id="dryRun"> Dry run (poll only, send nothing)</label>
      <button class="primary" id="btnLive" onclick="startBot()">▶ Start bot</button>
      <button class="danger" id="btnStop" onclick="stopBot()">■ Stop</button>
      <div class="spacer"></div>
      <button id="btnReset" onclick="resetState()">Reset state</button>
      <span class="muted" id="refreshNote">auto-refresh · 2s</span>
    </div>

    <div class="grid" id="stats"></div>

    <div class="cols">
      <div>
        <section><h2>Mirroring groups</h2>
          <table><thead><tr><th>Group</th><th>Sources</th><th>Destinations</th><th>Last ID</th></tr></thead><tbody id="groups"></tbody></table>
        </section>
        <section><h2>Your private configuration</h2>
          <pre id="configPreview">(empty — add your own config)</pre>
          <textarea id="yaml" spellcheck="false" placeholder="Paste YOUR config.yaml here. No template or sample data is provided."></textarea>
          <div class="row" style="margin-top:10px">
            <button class="small primary" onclick="saveConfig()">Save config</button>
            <button class="small" onclick="validateConfig()">Validate</button>
            <span class="msg" id="configMsg"></span>
          </div>
          <p class="muted" style="margin:10px 0 0">Secret lines stay <b>redacted</b> unless you replace them — saving keeps your existing values. Every save/start/stop/reset requires your explicit confirmation. Restart the bot to apply config changes.</p>
        </section>
      </div>
      <div>
        <section><h2>Event stream</h2><div class="events" id="events"><div class="muted">Waiting for events…</div></div></section>
      </div>
    </div>
    <footer>Local control panel · isolated per user · credentials are never rendered · respect Telegram's ToS</footer>
  </div>
</div>

<div class="overlay" id="overlay" onclick="if(event.target===this)closeAuth()">
  <div class="modal">
    <h3 id="authTitle">Sign in</h3><p id="authSub">Welcome back to your workspace.</p>
    <div class="tabs"><button id="tabLogin" class="on" onclick="authTab('login')">Sign in</button><button id="tabReg" onclick="authTab('register')">Create account</button></div>
    <input id="username" placeholder="Username" autocomplete="username">
    <input id="password" type="password" placeholder="Password (min 8 characters)" autocomplete="current-password">
    <div class="msg" id="authMsg"></div>
    <div class="row" style="margin-top:14px"><button class="primary" id="authSubmit" onclick="submitAuth()">Sign in</button><button class="ghost" onclick="closeAuth()">Cancel</button></div>
  </div>
</div>
<div id="toast"></div>

<script>
const QS = new URLSearchParams(location.search);
const SHARED_TOKEN = QS.get('token') || '';
const LS = { get:k=>{try{return localStorage.getItem(k)||'';}catch(e){return '';}},
             set:(k,v)=>{try{localStorage.setItem(k,v);}catch(e){}},
             del:k=>{try{localStorage.removeItem(k);}catch(e){}} };
const state = { guestKey: LS.get('tcd_guest'), token: LS.get('tcd_token'), theme: LS.get('tcd_theme')||'dark', me: null, authMode: 'login' };

function applyTheme(){ document.body.classList.toggle('light', state.theme==='light'); document.getElementById('theme').textContent = state.theme==='light'?'☀':'☾'; }
function toggleTheme(){ state.theme = state.theme==='light'?'dark':'light'; LS.set('tcd_theme',state.theme); applyTheme(); }
applyTheme();

async function api(path, opts={}){
  opts.headers = Object.assign({'Content-Type':'application/json','X-Token':SHARED_TOKEN,'X-Guest-Key':state.guestKey}, opts.headers||{});
  if(state.token) opts.headers['Authorization'] = 'Bearer '+state.token;
  const r = await fetch(path, opts);
  const ng = r.headers.get('X-New-Guest-Key');
  if(ng){ state.guestKey=ng; LS.set('tcd_guest',ng); }
  const ct = r.headers.get('Content-Type')||'';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if(!r.ok){ const e=new Error((data&&data.error)||('HTTP '+r.status)); e.status=r.status; e.data=data; throw e; }
  return data;
}
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg, kind=''){ const el=document.createElement('div'); el.className='toast '+kind; el.textContent=msg; document.getElementById('toast').appendChild(el); setTimeout(()=>{el.style.opacity=0;el.style.transform='translateX(30px)';setTimeout(()=>el.remove(),350);},3600); }
function busy(btn, on, text){ if(!btn) return; btn.disabled=on; if(on){btn.dataset.old=btn.innerHTML; btn.innerHTML='<span class="spin"></span>'+text;} else if(btn.dataset.old){btn.innerHTML=btn.dataset.old;} }

const LABELS=[['polls','Polls'],['mirrored','Mirrored'],['filtered','Filtered'],['edited','Edits'],['failed','Failures'],['retries','Retries']];
const nums={};
function animate(el,to){ const from=nums[el.id]||0; nums[el.id]=to; if(from===to){el.textContent=to;return;} const t0=performance.now();
  function step(t){const p=Math.min(1,(t-t0)/420); const e=1-Math.pow(1-p,3); el.textContent=Math.round(from+(to-from)*e); if(p<1)requestAnimationFrame(step);} requestAnimationFrame(step); }

async function refresh(){
  try{
    const [s,ev,cfg] = await Promise.all([api('/api/status'),api('/api/events?limit=60'),api('/api/config')]);
    const st=document.getElementById('state'); st.textContent=s.state; st.className='pill '+(s.state==='running'?'running':s.state==='error'?'error':'');
    st.innerHTML='<span class="dot"></span>'+esc(s.state);
    const md=document.getElementById('mode'); md.textContent=s.mode; md.className='pill';
    document.getElementById('sub').textContent = s.error ? s.error : (s.state==='running' ? 'Running '+s.uptime_seconds+'s · private workspace' : 'Idle · your data is private');
    const grid=document.getElementById('stats');
    if(!grid.children.length) grid.innerHTML = LABELS.map(([k,l])=>'<div class="card"><div class="n" id="n_'+k+'">0</div><div class="l">'+l+'</div></div>').join('');
    LABELS.forEach(([k])=>animate(document.getElementById('n_'+k),(s.stats.counters||{})[k]||0));
    const pos={}; (s.stats.sources||[]).forEach(x=>pos[x.key]=x.last_message_id);
    document.getElementById('groups').innerHTML=(s.groups||[]).map(g=>{
      const last=g.sources.map(src=>{const k=Object.keys(pos).find(k=>k.endsWith('::'+src)); return k?pos[k]:'—';});
      return '<tr><td><b>'+esc(g.name)+'</b><div class="muted">whitelist: '+(g.whitelist.length?esc(g.whitelist.join(', ')):'off')+'</div></td>'+
        '<td>'+g.sources.map(x=>'<span class="tag">'+esc(x)+'</span>').join('')+'</td>'+
        '<td>'+g.destinations.map(x=>'<span class="tag">'+esc(x)+'</span>').join('')+'</td>'+
        '<td class="mono">'+esc(last.join(', '))+'</td></tr>';
    }).join('')||'<tr><td colspan="4" class="muted">No groups configured</td></tr>';
    const ta=document.getElementById('yaml'); if(!ta.dataset.dirty) ta.value=cfg.redacted||'';
    document.getElementById('configPreview').textContent=cfg.redacted||'(no config)';
    document.getElementById('events').innerHTML=(ev.events||[]).slice().reverse().map(e=>
      '<div class="ev '+esc(e.level)+'"><span class="t">'+esc((e.time||'').slice(11,19))+'</span><span class="k">'+esc(e.kind)+'</span><span>'+esc(e.message)+'</span></div>').join('')||'<div class="muted">No events yet.</div>';
  }catch(e){ document.getElementById('sub').textContent='Refresh failed: '+e.message; }
}

async function startBot(){ const dry=document.getElementById('dryRun').checked;
  if(!confirm(dry ? 'Start DRY RUN? It will connect and poll but send nothing.' :
                    'Start the LIVE bot? It will connect to Telegram and send messages to your destinations.')) return;
  const btn=document.getElementById('btnLive'); busy(btn,true,'Starting');
  try{ const r=await api('/api/start',{method:'POST',body:JSON.stringify({confirm:true,dry_run:dry})});
       toast(r.message||(r.ok?'Started':'Could not start'),r.ok?'ok':'err'); }
  catch(e){toast(e.message,'err');} finally{busy(btn,false);await refresh();} }
async function stopBot(){ if(!confirm('Stop your bot?'))return;
  const b=document.getElementById('btnStop'); busy(b,true,'Stopping');
  try{const r=await api('/api/stop',{method:'POST',body:JSON.stringify({confirm:true})}); toast(r.message||'Stopped','ok');}catch(e){toast(e.message,'err');}finally{busy(b,false);await refresh();} }
async function resetState(){ if(!confirm('Clear positions, counters and message map for YOUR workspace only?'))return;
  try{await api('/api/reset',{method:'POST',body:JSON.stringify({confirm:true})});toast('Your state was reset','ok');await refresh();}catch(e){toast(e.message,'err');} }

function cfgMsg(text,kind){const el=document.getElementById('configMsg'); el.className='msg '+kind; el.textContent=text;}
async function validateConfig(){ try{const r=await api('/api/config/validate',{method:'POST',body:JSON.stringify({yaml:document.getElementById('yaml').value})});
  cfgMsg(r.ok?'Valid · '+r.groups+' group(s), delay '+r.delay+'s':'Invalid · '+r.error, r.ok?'ok':'err');}catch(e){cfgMsg(e.message,'err');} }
async function saveConfig(){ if(!confirm('Save this config to YOUR private workspace?'))return;
  try{const r=await api('/api/config',{method:'POST',body:JSON.stringify({confirm:true,yaml:document.getElementById('yaml').value})});
  cfgMsg('Saved · '+r.message,'ok'); document.getElementById('yaml').dataset.dirty=''; toast('Config saved. Restart the bot to apply.','ok'); await refresh();}
  catch(e){cfgMsg(e.message,'err');toast(e.message,'err');} }
document.getElementById('yaml').addEventListener('input',e=>e.target.dataset.dirty='1');

function openAuth(mode){ authTab(mode||'login'); document.getElementById('overlay').classList.add('show'); setTimeout(()=>document.getElementById('username').focus(),250); }
function closeAuth(){ document.getElementById('overlay').classList.remove('show'); document.getElementById('authMsg').textContent=''; }
function authTab(mode){ state.authMode=mode; const login=mode==='login';
  document.getElementById('tabLogin').classList.toggle('on',login); document.getElementById('tabReg').classList.toggle('on',!login);
  document.getElementById('authTitle').textContent=login?'Sign in':'Create your account';
  document.getElementById('authSub').textContent=login?'Welcome back to your private workspace.':'Your own isolated config, state and Telegram session.';
  document.getElementById('authSubmit').textContent=login?'Sign in':'Create account';
  document.getElementById('password').autocomplete=login?'current-password':'new-password';
  document.getElementById('authMsg').textContent=''; }
async function submitAuth(){ const u=document.getElementById('username').value,p=document.getElementById('password').value;
  const btn=document.getElementById('authSubmit'); busy(btn,true,'Please wait');
  try{ const r=await api('/api/auth/'+(state.authMode==='login'?'login':'register'),{method:'POST',body:JSON.stringify({username:u,password:p})});
    state.token=r.token; LS.set('tcd_token',r.token); state.me=r.identity; closeAuth();
    document.getElementById('gate').classList.add('hidden'); document.getElementById('app').classList.remove('hidden');
    toast('Welcome, '+r.identity.username+'! Your workspace is private.','ok'); await init(); }
  catch(e){document.getElementById('authMsg').className='msg err'; document.getElementById('authMsg').textContent=e.message;}
  finally{busy(btn,false);} }
async function logout(){ try{await api('/api/auth/logout',{method:'POST',body:'{}'});}catch(e){}
  state.token=''; LS.del('tcd_token'); toast('Signed out','ok'); await init(); }

async function init(){ try{ const me=await api('/api/me'); state.me=me.identity;
  const guest=me.identity.is_guest;
  document.getElementById('who').textContent=guest?'Guest · '+(me.identity.short||'private'):me.identity.username;
  document.getElementById('guestBanner').style.display=guest?'flex':'none';
  document.getElementById('logoutBtn').style.display=guest?'none':'inline-block';
  document.getElementById('authBtn').style.display=guest?'inline-block':'none';
  if(me.require_login&&guest){ document.getElementById('gate').classList.remove('hidden'); document.getElementById('app').classList.add('hidden'); return; }
  document.getElementById('gate').classList.add('hidden'); document.getElementById('app').classList.remove('hidden');
  await refresh();
}catch(e){ toast('Connection failed: '+e.message,'err'); } }
init(); setInterval(refresh,2000);
</script>
</body></html>
"""

_SECRET_LINE = re.compile(r"^\s*(account_phone|api_id|api_hash)\s*:", re.I)
REDACTED = '"*** redacted ***"'


def redact_config(text: str) -> str:
    out = []
    for line in text.splitlines():
        match = _SECRET_LINE.match(line)
        out.append(f"{match.group(0)} {REDACTED}" if match else line)
    return "\n".join(out)


def merge_config_text(new_text: str, old_text: str) -> str:
    """Keep hidden secrets when the editor still shows the redaction marker."""
    secrets = {}
    for line in old_text.splitlines():
        match = _SECRET_LINE.match(line)
        if match:
            secrets[match.group(1).lower()] = line
    out = []
    for line in new_text.splitlines():
        match = _SECRET_LINE.match(line)
        if match and "redacted" in line and match.group(1).lower() in secrets:
            out.append(secrets[match.group(1).lower()])
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if new_text.endswith("\n") else "")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        manager: WorkspaceManager,
        *,
        token: Optional[str] = None,
        require_login: bool = False,
        secure_cookies: bool = False,
    ):
        super().__init__(address, DashboardHandler)
        self.manager = manager
        self.token = token
        self.require_login = require_login
        self.secure_cookies = secure_cookies


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DuplicatorDashboard/2.0"

    def log_message(self, *_args):
        pass

    # ------------------------------------------------------------- plumbing
    def _begin(self):
        self._set_cookies = []
        self._new_guest_key = None
        self._identity_cache = None

    def _cookie(self, name: str, value: str, max_age: int) -> str:
        same_site = "None" if self.server.secure_cookies else "Lax"
        parts = [f"{name}={value}", "Path=/", "HttpOnly", f"SameSite={same_site}", f"Max-Age={max_age}"]
        if self.server.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def _headers(self, content_type="application/json; charset=utf-8"):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token, X-Guest-Key, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for cookie in getattr(self, "_set_cookies", []):
            self.send_header("Set-Cookie", cookie)
        if getattr(self, "_new_guest_key", None):
            self.send_header("X-New-Guest-Key", self._new_guest_key)

    def _json(self, payload: Any, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self._headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _cookies(self) -> SimpleCookie:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except Exception:  # malformed cookie header
            pass
        return jar

    def _shared_token_ok(self) -> bool:
        token = self.server.token
        if not token:
            return True
        supplied = self.headers.get("X-Token") or parse_qs(urlparse(self.path).query).get("token", [None])[0]
        return supplied == token

    def _identity(self) -> Identity:
        if self._identity_cache is not None:
            return self._identity_cache
        jar = self._cookies()
        auth_header = self.headers.get("Authorization") or ""
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else None
        if not token and "tcd_token" in jar:
            token = jar["tcd_token"].value

        identity = self.server.manager.auth.identity_for_token(token)
        if identity is not None:
            self._identity_cache = identity
            return identity

        guest = self.headers.get("X-Guest-Key")
        if not valid_guest_id(guest) and "tcd_guest" in jar:
            guest = jar["tcd_guest"].value
        if not valid_guest_id(guest):
            guest = new_guest_id()
            self._new_guest_key = guest
            self._set_cookies.append(self._cookie("tcd_guest", guest, 60 * 60 * 24 * 365))
        identity = Identity(key=guest, username=None, is_guest=True)
        self._identity_cache = identity
        return identity

    def _workspace(self, identity: Identity):
        return self.server.manager.workspace(identity.key)

    def _guard(self):
        """Return an error payload when the request may not proceed."""
        if not self._shared_token_ok():
            return {"error": "unauthorized"}, 401
        identity = self._identity()
        if self.server.require_login and identity.is_guest:
            return {"error": "login required"}, 401
        return None, None

    # -------------------------------------------------------------- routes
    def do_OPTIONS(self):
        self._begin()
        self.send_response(204)
        self._headers()
        self.end_headers()

    def do_GET(self):
        self._begin()
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            body = PAGE.encode("utf8")
            self.send_response(200)
            self._headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            return self._json({"ok": True})

        # /api/me must stay reachable for guests so the UI can show the login gate.
        if path == "/api/me":
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            identity = self._identity()
            return self._json(
                {
                    "identity": {
                        "username": identity.username,
                        "is_guest": identity.is_guest,
                        "short": identity.key[:9] if identity.is_guest else None,
                    },
                    "require_login": self.server.require_login,
                    "allow_registration": self.server.manager.auth.allow_registration,
                }
            )

        error, status = self._guard()
        if error:
            return self._json(error, status)
        identity = self._identity()
        ws = self._workspace(identity)

        if path == "/api/status":
            data = ws.runner.status()
            data["stats"] = ws.store.stats()
            data["identity"] = {"username": identity.username, "is_guest": identity.is_guest}
            return self._json(data)
        if path == "/api/events":
            limit = int(query.get("limit", [100])[0])
            since = int(query.get("since_id", [0])[0])
            return self._json({"events": ws.store.events(limit=limit, since_id=since)})
        if path == "/api/config":
            raw = self._read_config(ws)
            try:
                ConfigController.validate_text(raw, use_dotenv=True, allow_unresolved_env=True)
                error = None
            except ConfigError as exc:
                error = str(exc)
            return self._json(
                {
                    "redacted": redact_config(raw),
                    "validation_error": error,
                    "env_missing": ConfigController.missing_env(raw),
                }
            )
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        self._begin()
        path = urlparse(self.path).path
        body = self._body()
        manager = self.server.manager

        # Auth endpoints must work even when login is required.
        if path in ("/api/auth/register", "/api/auth/login"):
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            try:
                if path.endswith("register"):
                    manager.auth.register(str(body.get("username", "")), str(body.get("password", "")))
                token, identity = manager.auth.login(str(body.get("username", "")), str(body.get("password", "")))
            except AuthError as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            self._set_cookies.append(self._cookie("tcd_token", token, 60 * 60 * 24 * 30))
            self._set_cookies.append(self._cookie("tcd_guest", "", 0))
            self._identity_cache = identity
            return self._json(
                {"ok": True, "token": token, "identity": {"username": identity.username, "is_guest": False}}
            )
        if path == "/api/auth/logout":
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            identity = self._identity()
            manager.auth.logout(identity.token)
            self._set_cookies.append(self._cookie("tcd_token", "", 0))
            return self._json({"ok": True})

        error, status = self._guard()
        if error:
            return self._json(error, status)
        identity = self._identity()
        ws = self._workspace(identity)

        # No pre-approved actions: state-changing calls require an explicit flag.
        if path in ("/api/start", "/api/stop", "/api/reset", "/api/config"):
            if body.get("confirm") is not True:
                return self._json({"error": "explicit confirmation required"}, 400)

        if path == "/api/start":
            dry_run = bool(body.get("dry_run", ws.runner.dry_run))
            ok, message = ws.runner.start(dry_run=dry_run)
            return self._json({"ok": ok, "message": message, "status": ws.runner.status()})
        if path == "/api/stop":
            ok, message = ws.runner.stop()
            return self._json({"ok": ok, "message": message})
        if path == "/api/reset":
            ws.store.reset()
            return self._json({"ok": True, "message": "state reset"})
        if path == "/api/config/validate":
            try:
                config = ConfigController.validate_text(
                    str(body.get("yaml", "")), use_dotenv=True, allow_unresolved_env=True
                )
                return self._json({"ok": True, "groups": len(config["groups"]), "delay": config["delay"]})
            except ConfigError as exc:
                return self._json({"ok": False, "error": str(exc)})
        if path == "/api/config":
            old = self._read_config(ws)
            merged = merge_config_text(str(body.get("yaml", "")), old)
            try:
                config = ConfigController.validate_text(merged, use_dotenv=True, allow_unresolved_env=True)
            except ConfigError as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            with open(ws.paths["config"], "w", encoding="utf8") as f:
                f.write(merged)
            os.chmod(ws.paths["config"], 0o600)
            ws.store.event("config", f"Configuration updated: {len(config['groups'])} group(s)", "SUCCESS")
            return self._json({"ok": True, "message": f"{len(config['groups'])} group(s) validated"})
        return self._json({"error": "not found"}, 404)

    @staticmethod
    def _read_config(ws) -> str:
        try:
            with open(ws.paths["config"], "r", encoding="utf8") as f:
                return f.read()
        except OSError:
            return ""


def serve(
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    data_dir: str = "data",
    dry_run: bool = False,
    token: Optional[str] = None,
    require_login: bool = False,
    allow_registration: bool = True,
    secure_cookies: bool = False,
    bot_factory=None,
) -> None:
    """Blocking entry point used by the CLI."""
    manager = WorkspaceManager(
        data_dir,
        dry_run=dry_run,
        allow_registration=allow_registration,
        bot_factory=bot_factory,
    )
    server = DashboardServer(
        (host, port),
        manager,
        token=token,
        require_login=require_login,
        secure_cookies=secure_cookies,
    )
    logger.info(
        f"Dashboard http://{host}:{port} | data={manager.data_dir} | "
        f"login_required={require_login} | autostart=off | token={'yes' if token else 'no'}"
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        manager.close()
        server.server_close()
