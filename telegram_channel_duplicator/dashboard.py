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
<title>Relay — Channel Duplicator</title>
<style>
*{box-sizing:border-box}
html{scroll-behavior:smooth}
:root{
  --bg:#F4F1EA;--surface:#FFFFFF;--surface2:#EBE6DB;--ink:#1B1A17;--muted:#6C685F;
  --line:#DAD4C7;--accent:#A83F1C;--accent2:#F3E1D7;--on-accent:#FFFFFF;
  --ok:#2E7A55;--warn:#A97615;--err:#AE3737;
  --shadow:0 1px 2px rgba(27,26,23,.05),0 8px 24px rgba(27,26,23,.04);
  --r:10px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--serif:Georgia,'Times New Roman',serif;
}
body[data-theme='ocean']{--bg:#EEF3F7;--surface:#FFFFFF;--surface2:#E1EAF2;--ink:#12253A;--muted:#5B6C7E;--line:#CFDCE8;--accent:#12639E;--accent2:#DAEAF7;--on-accent:#FFFFFF;--ok:#1E7A5C;--warn:#9A6A12;--err:#B23A45;--shadow:0 1px 2px rgba(18,37,58,.05),0 8px 24px rgba(18,37,58,.05)}
body[data-theme='slate']{--bg:#14181D;--surface:#1C222A;--surface2:#242C36;--ink:#EDF1F5;--muted:#99A4B1;--line:#323B46;--accent:#5CC6AC;--accent2:#223A38;--on-accent:#0B1512;--ok:#63C79B;--warn:#D8AC5A;--err:#E68080;--shadow:0 1px 2px rgba(0,0,0,.22),0 8px 24px rgba(0,0,0,.18)}
body[data-theme='ember']{--bg:#191510;--surface:#221C16;--surface2:#2C2419;--ink:#F6EFE6;--muted:#AC9D89;--line:#3B3125;--accent:#E7A24A;--accent2:#3A2D18;--on-accent:#1A1206;--ok:#84C37E;--warn:#E0B04A;--err:#E0776B;--shadow:0 1px 2px rgba(0,0,0,.25),0 8px 24px rgba(0,0,0,.2)}
body.light{color-scheme:light}
body:not(.light){color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;transition:background-color .45s cubic-bezier(.4,0,.2,1),color .45s cubic-bezier(.4,0,.2,1)}
a{color:inherit;text-decoration:none}
button{font:inherit;cursor:pointer}
.hidden{display:none!important}
.mono{font-family:var(--mono)}
.muted{color:var(--muted)}

.shell{display:grid;grid-template-columns:248px 1fr;min-height:100vh}
.side{position:sticky;top:0;height:100vh;background:var(--surface);border-right:1px solid var(--line);padding:26px 18px;display:flex;flex-direction:column;transition:background-color .45s cubic-bezier(.4,0,.2,1),border-color .45s}
.brand{display:flex;align-items:center;gap:11px;padding:0 8px}
.brand svg{width:34px;height:34px;color:var(--accent);flex:none}
.brand-name{font-family:var(--serif);font-size:23px;line-height:1;font-weight:600;letter-spacing:-.02em}
.brand-sub{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-top:4px}
nav{display:flex;flex-direction:column;gap:3px;margin-top:36px}
.nav{position:relative;display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:8px;color:var(--muted);font-size:13px;font-weight:550;border:1px solid transparent;transition:background .22s cubic-bezier(.4,0,.2,1),color .22s,border-color .22s,transform .22s}
.nav svg{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.nav:hover{background:var(--surface2);color:var(--ink);transform:translateX(2px)}
.nav.on{background:var(--accent2);color:var(--accent);border-color:var(--line)}
.nav.on::before{content:'';position:absolute;left:-18px;top:50%;transform:translateY(-50%);width:3px;height:20px;background:var(--accent);border-radius:0 3px 3px 0}
.side-foot{margin-top:auto}
.side-label{font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);margin-bottom:11px}
.swatches{display:flex;gap:9px;align-items:center}
.swatch{width:22px;height:22px;border-radius:50%;border:2px solid var(--line);background:var(--sw);padding:0;transition:transform .2s cubic-bezier(.4,0,.2,1),box-shadow .2s}
.swatch:hover{transform:scale(1.14)}
.swatch.on{box-shadow:0 0 0 2px var(--surface),0 0 0 4px var(--accent)}
.side-note{margin-top:22px;padding:11px 12px;border:1px dashed var(--line);border-radius:8px;font-size:11px;color:var(--muted);line-height:1.6}
.side-note b{display:block;color:var(--ink);font-family:var(--mono);font-size:10.5px;font-weight:600;margin-top:3px;word-break:break-all}

.main{min-width:0}
.top{position:sticky;top:0;z-index:20;height:64px;background:var(--surface);border-bottom:1px solid var(--line);padding:0 32px;display:flex;align-items:center;justify-content:space-between;gap:16px;transition:background-color .45s cubic-bezier(.4,0,.2,1)}
.crumb{font-size:12.5px;color:var(--muted);white-space:nowrap}
.crumb b{color:var(--ink);font-weight:600}
.crumb i{font-style:normal;margin:0 9px;color:var(--line)}
.top-right{display:flex;align-items:center;gap:9px}
.live{display:flex;align-items:center;gap:7px;font-size:11.5px;color:var(--muted);margin-right:6px;white-space:nowrap}
.live i{width:6px;height:6px;border-radius:50%;background:var(--ok);animation:pulse 2.4s infinite}
.content{padding:30px 32px 44px;max-width:1420px}

.banner{display:flex;align-items:center;gap:12px;background:var(--accent2);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:var(--r);padding:12px 16px;margin-bottom:24px;font-size:12.5px;line-height:1.5}
.banner .spacer{flex:1}

.hero{display:grid;grid-template-columns:1.35fr 1fr;gap:32px;align-items:end;padding:6px 0 28px;margin-bottom:26px;border-bottom:1px solid var(--line)}
.eyebrow{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-family:var(--serif);font-size:44px;line-height:1.08;letter-spacing:-.025em;font-weight:600;margin:12px 0 8px}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 18px;min-height:20px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--surface);border-radius:100px;padding:6px 13px;font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;transition:background .3s,border-color .3s}
.chip .dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex:none}
.chip.running .dot{background:var(--ok);animation:pulse 1.8s infinite}
.chip.error .dot{background:var(--err)}
.chip.ghost{background:transparent;color:var(--muted)}
.hero-actions{display:flex;flex-direction:column;gap:15px;align-items:flex-end}
.switch{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--muted);cursor:pointer;user-select:none}
.switch input{appearance:none;width:36px;height:20px;border-radius:100px;background:var(--surface2);border:1px solid var(--line);position:relative;cursor:pointer;flex:none;transition:background .25s cubic-bezier(.4,0,.2,1),border-color .25s}
.switch input::after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:var(--muted);transition:transform .25s cubic-bezier(.4,0,.2,1),background .25s}
.switch input:checked{background:var(--accent);border-color:var(--accent)}
.switch input:checked::after{transform:translateX(16px);background:var(--on-accent)}
.row{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:9px 15px;border-radius:8px;font-size:12.5px;font-weight:600;white-space:nowrap;transition:transform .18s cubic-bezier(.4,0,.2,1),box-shadow .18s,border-color .18s,color .18s,background .18s}
.btn:hover{border-color:var(--accent);transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.btn.primary:hover{box-shadow:0 6px 18px rgba(0,0,0,.16)}
.btn.subtle{background:transparent;color:var(--muted)}
.btn.subtle:hover{color:var(--ink)}
.btn.danger:hover{border-color:var(--err);color:var(--err)}
.btn:disabled{opacity:.55;cursor:wait;transform:none}
.btn.small{padding:7px 12px;font-size:11.5px}
button:focus-visible,input:focus-visible,textarea:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:26px}
.metric{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px 16px 13px;box-shadow:var(--shadow);position:relative;overflow:hidden;transition:transform .22s cubic-bezier(.4,0,.2,1),border-color .22s,background-color .45s}
.metric:hover{transform:translateY(-2px);border-color:var(--accent)}
.metric .k{font-size:10px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted)}
.metric .n{font-family:var(--mono);font-size:27px;font-weight:600;letter-spacing:-.045em;line-height:1;margin-top:9px}
.metric .bar{height:3px;background:var(--surface2);border-radius:2px;margin-top:15px;overflow:hidden}
.metric .bar i{display:block;height:100%;width:0;background:var(--accent);border-radius:2px;transition:width .65s cubic-bezier(.4,0,.2,1)}
.metric.is-err .n{color:var(--err)}

.cols{display:grid;grid-template-columns:1.55fr 1fr;gap:18px;align-items:start}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:20px;margin-bottom:18px;scroll-margin-top:80px;transition:background-color .45s cubic-bezier(.4,0,.2,1)}
.panel-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.panel h2{font-size:15px;margin:0;font-weight:650;letter-spacing:-.01em}
.panel-meta{font-family:var(--mono);font-size:10.5px;color:var(--muted);white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.11em;color:var(--muted);font-weight:600;padding:0 10px 10px 0;border-bottom:1px solid var(--line)}
td{padding:11px 10px 11px 0;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td b{font-weight:650}
.tag{display:inline-block;background:var(--surface2);border:1px solid var(--line);border-radius:5px;padding:2px 7px;font-family:var(--mono);font-size:10.5px;margin:2px 4px 2px 0}
#configPreview{background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:12px;font:11.5px/1.6 var(--mono);white-space:pre-wrap;word-break:break-word;max-height:150px;overflow:auto;color:var(--muted);margin:0 0 12px}
textarea{width:100%;min-height:180px;resize:vertical;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:12px;font:12px/1.6 var(--mono);transition:border-color .2s cubic-bezier(.4,0,.2,1)}
textarea:focus{border-color:var(--accent)}
.msg{font-size:12px;margin-left:4px}
.msg.ok{color:var(--ok)}.msg.err{color:var(--err)}.msg.warn{color:var(--warn)}
.hint{font-size:11.5px;color:var(--muted);line-height:1.65;margin:12px 0 0}
.timeline{max-height:540px;overflow:auto}
.ev{display:grid;grid-template-columns:62px 1fr;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);animation:fadeUp .35s cubic-bezier(.4,0,.2,1) both}
.ev:last-child{border-bottom:0}
.ev .t{font-family:var(--mono);font-size:10px;color:var(--muted);padding-top:3px}
.ev .body{min-width:0}
.ev .k{display:inline-block;font-size:9px;text-transform:uppercase;letter-spacing:.11em;color:var(--accent);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.ev .tx{display:block;margin-top:6px;font-size:12px;color:var(--ink);word-break:break-word;line-height:1.5}
.ev.ERROR .k{color:var(--err);border-color:var(--err)}
.ev.SUCCESS .k{color:var(--ok)}
.ev.WARNING .k{color:var(--warn)}
footer{color:var(--muted);font-size:11px;padding-top:8px;border-top:1px solid var(--line);margin-top:6px;line-height:1.7}

.gate{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);padding:20px}
.gate-card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:36px;max-width:420px;width:100%;box-shadow:var(--shadow)}
.gate-card h2{font-family:var(--serif);font-size:27px;margin:18px 0 8px;font-weight:600;letter-spacing:-.02em}
.gate-card p{color:var(--muted);font-size:13px;margin:0 0 22px;line-height:1.6}
.overlay{position:fixed;inset:0;background:rgba(18,16,13,.55);display:none;align-items:center;justify-content:center;padding:20px;z-index:50}
.overlay.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:26px;width:min(420px,100%);box-shadow:0 24px 70px rgba(0,0,0,.28);animation:pop .28s cubic-bezier(.4,0,.2,1)}
.modal h3{font-family:var(--serif);font-size:23px;margin:0 0 4px;font-weight:600}
.modal p{color:var(--muted);font-size:12.5px;margin:0 0 18px;line-height:1.55}
.tabs{display:flex;border:1px solid var(--line);border-radius:8px;padding:3px;margin-bottom:16px;background:var(--surface2)}
.tabs button{flex:1;border:0;background:transparent;color:var(--muted);padding:8px;border-radius:6px;font-size:12px;font-weight:600;transition:background .2s,color .2s,box-shadow .2s}
.tabs button.on{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}
.modal input{width:100%;margin-bottom:10px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--ink);font-size:13px}
#toast{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:9px;z-index:80}
.toast{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:11px 15px;font-size:12.5px;box-shadow:var(--shadow);min-width:220px;max-width:360px;line-height:1.5;animation:slideIn .3s cubic-bezier(.4,0,.2,1);transition:opacity .3s,transform .3s}
.toast.ok{border-left-color:var(--ok)}.toast.err{border-left-color:var(--err)}
.spin{display:inline-block;width:11px;height:11px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite;margin-right:6px;vertical-align:-1px}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeUp{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
@keyframes pop{from{opacity:0;transform:scale(.97) translateY(8px)}to{opacity:1;transform:none}}
@keyframes slideIn{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}
@media(max-width:1180px){.metrics{grid-template-columns:repeat(3,1fr)}}
@media(max-width:980px){
  .shell{grid-template-columns:1fr}
  .side{position:static;height:auto;flex-direction:row;align-items:center;gap:14px;padding:14px 18px;border-right:0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  nav{flex-direction:row;margin:0;gap:4px;flex-wrap:wrap}
  .nav.on::before{display:none}
  .side-foot{display:none}
  .top{padding:0 18px}.content{padding:20px 18px 34px}
  .hero{grid-template-columns:1fr;gap:20px;align-items:start}
  .hero-actions{align-items:flex-start}
  .cols{grid-template-columns:1fr}
  h1{font-size:34px}
}
@media(max-width:560px){.metrics{grid-template-columns:repeat(2,1fr)}.live{display:none}h1{font-size:29px}.crumb{display:none}}
</style>
</head>
<body data-theme="paper" class="light">

<div id="gate" class="gate hidden">
  <div class="gate-card">
    <div class="brand">
      <svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><rect x="3.5" y="3.5" width="24" height="24" rx="5" stroke="currentColor" stroke-width="2.3"/><rect x="12.5" y="12.5" width="24" height="24" rx="5" fill="currentColor" fill-opacity=".15" stroke="currentColor" stroke-width="2.3"/><path d="M18 24h12m-4.5-4.5L30 24l-4.5 4.5" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <div><div class="brand-name">Relay</div><div class="brand-sub">channel duplicator</div></div>
    </div>
    <h2>Sign in to continue</h2>
    <p>This server requires an account. Each account has a completely private workspace — config, sessions and history are never shared.</p>
    <div class="row"><button class="btn primary" onclick="openAuth('login')">Sign in</button><button class="btn" onclick="openAuth('register')">Create account</button></div>
  </div>
</div>

<div id="app" class="shell">
  <aside class="side">
    <div class="brand">
      <svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><rect x="3.5" y="3.5" width="24" height="24" rx="5" stroke="currentColor" stroke-width="2.3"/><rect x="12.5" y="12.5" width="24" height="24" rx="5" fill="currentColor" fill-opacity=".15" stroke="currentColor" stroke-width="2.3"/><path d="M18 24h12m-4.5-4.5L30 24l-4.5 4.5" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <div><div class="brand-name">Relay</div><div class="brand-sub">channel duplicator</div></div>
    </div>
    <nav>
      <a class="nav on" href="#overview" onclick="navTo(this,'Overview')"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>Overview</a>
      <a class="nav" href="#groups" onclick="navTo(this,'Channels')"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg>Channels</a>
      <a class="nav" href="#config" onclick="navTo(this,'Configuration')"><svg viewBox="0 0 24 24"><path d="M8 4h8l4 4v12H4V4h4z"/><path d="M8 12h8M8 16h5"/></svg>Configuration</a>
      <a class="nav" href="#activity" onclick="navTo(this,'Activity')"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>Activity</a>
    </nav>
    <div class="side-foot">
      <div class="side-label">Appearance</div>
      <div class="swatches">
        <button class="swatch on" data-theme-choice="paper" style="--sw:#A83F1C" title="Paper — warm light" onclick="setTheme('paper')"></button>
        <button class="swatch" data-theme-choice="ocean" style="--sw:#12639E" title="Ocean — cool light" onclick="setTheme('ocean')"></button>
        <button class="swatch" data-theme-choice="slate" style="--sw:#5CC6AC" title="Slate — dark neutral" onclick="setTheme('slate')"></button>
        <button class="swatch" data-theme-choice="ember" style="--sw:#E7A24A" title="Ember — dark warm" onclick="setTheme('ember')"></button>
      </div>
      <div class="side-note">Private workspace<b id="who">connecting…</b></div>
    </div>
  </aside>

  <div class="main">
    <header class="top">
      <div class="crumb">Control Panel<i>/</i><b id="pageTitle">Overview</b></div>
      <div class="top-right">
        <span class="live"><i></i>auto-refresh · 2s</span>
        <button class="btn subtle small" id="themeBtn" onclick="toggleTheme()">◐ Paper</button>
        <button class="btn subtle small" id="authBtn" onclick="openAuth('login')">Sign in</button>
        <button class="btn subtle small" id="logoutBtn" style="display:none" onclick="logout()">Sign out</button>
      </div>
    </header>

    <div class="content">
      <div class="banner" id="guestBanner" style="display:none">
        <span>👋 You are in a <b>private guest workspace</b>. Nobody else can see it. Create an account to keep it across devices.</span>
        <div class="spacer"></div>
        <button class="btn small primary" onclick="openAuth('register')">Create account</button>
      </div>

      <section class="hero" id="overview">
        <div class="hero-copy">
          <div class="eyebrow">Private workspace · isolated by account</div>
          <h1>Your channels, mirrored.</h1>
          <p class="sub" id="sub">Idle · your data is private</p>
          <div class="chips">
            <span class="chip" id="state"><i class="dot"></i>stopped</span>
            <span class="chip ghost" id="mode">live</span>
          </div>
        </div>
        <div class="hero-actions">
          <label class="switch"><input type="checkbox" id="dryRun"><span>Dry run — poll only, send nothing</span></label>
          <div class="row">
            <button class="btn primary" id="btnLive" onclick="startBot()">Start bot</button>
            <button class="btn" id="btnStop" onclick="stopBot()">Stop</button>
            <button class="btn subtle" onclick="resetState()">Reset state</button>
          </div>
        </div>
      </section>

      <section class="metrics" id="stats"></section>

      <div class="cols">
        <div>
          <section class="panel" id="groups">
            <div class="panel-head"><h2>Mirroring groups</h2><span class="panel-meta">last known positions</span></div>
            <table><thead><tr><th>Group</th><th>Sources</th><th>Destinations</th><th>Last ID</th></tr></thead><tbody id="groupsBody"></tbody></table>
          </section>
          <section class="panel" id="config">
            <div class="panel-head"><h2>Configuration</h2><span class="panel-meta">private · secrets redacted</span></div>
            <pre id="configPreview">(empty — add your own config)</pre>
            <textarea id="yaml" spellcheck="false" placeholder="Paste YOUR config.yaml here. No template or sample data is provided."></textarea>
            <div class="row" style="margin-top:11px">
              <button class="btn small primary" onclick="saveConfig()">Save config</button>
              <button class="btn small" onclick="validateConfig()">Validate</button>
              <span class="msg" id="configMsg"></span>
            </div>
            <p class="hint">Secret lines stay <b>redacted</b> unless you replace them — saving keeps your existing values. Every save, start, stop and reset needs your explicit confirmation. Restart the bot to apply config changes.</p>
          </section>
        </div>
        <div>
          <section class="panel" id="activity">
            <div class="panel-head"><h2>Activity</h2><span class="panel-meta">newest first</span></div>
            <div class="timeline" id="events"><div class="muted" style="font-size:12px">No events yet.</div></div>
          </section>
        </div>
      </div>
      <footer>Relay · local control panel · isolated per user · credentials are never rendered · respect Telegram's Terms of Service</footer>
    </div>
  </div>
</div>

<div class="overlay" id="overlay" onclick="if(event.target===this)closeAuth()">
  <div class="modal">
    <h3 id="authTitle">Sign in</h3><p id="authSub">Welcome back to your workspace.</p>
    <div class="tabs"><button id="tabLogin" class="on" onclick="authTab('login')">Sign in</button><button id="tabReg" onclick="authTab('register')">Create account</button></div>
    <input id="username" placeholder="Username" autocomplete="username">
    <input id="password" type="password" placeholder="Password (min 8 characters)" autocomplete="current-password">
    <div class="msg" id="authMsg"></div>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="authSubmit" onclick="submitAuth()">Sign in</button><button class="btn subtle" onclick="closeAuth()">Cancel</button></div>
  </div>
</div>
<div id="toast"></div>

<script>
const QS = new URLSearchParams(location.search);
const SHARED_TOKEN = QS.get('token') || '';
const LS = { get:k=>{try{return localStorage.getItem(k)||'';}catch(e){return '';}},
             set:(k,v)=>{try{localStorage.setItem(k,v);}catch(e){}},
             del:k=>{try{localStorage.removeItem(k);}catch(e){}} };
const THEMES = [['paper','Paper'],['ocean','Ocean'],['slate','Slate'],['ember','Ember']];
const state = { guestKey: LS.get('tcd_guest'), token: LS.get('tcd_token'), theme: LS.get('tcd_theme')||'paper', me: null, authMode: 'login' };
if(!THEMES.some(t=>t[0]===state.theme)) state.theme='paper';

function applyTheme(){
  document.body.dataset.theme = state.theme;
  document.body.classList.toggle('light', state.theme==='paper'||state.theme==='ocean');
  const t = THEMES.find(x=>x[0]===state.theme)||THEMES[0];
  const b=document.getElementById('themeBtn'); if(b) b.textContent='◐ '+t[1];
  document.querySelectorAll('.swatch').forEach(el=>el.classList.toggle('on',el.dataset.themeChoice===state.theme));
}
function setTheme(name){ state.theme=name; LS.set('tcd_theme',name); applyTheme(); }
function toggleTheme(){ const i=THEMES.findIndex(x=>x[0]===state.theme); setTheme(THEMES[(i+1)%THEMES.length][0]); }
function navTo(el,title){ document.querySelectorAll('.nav').forEach(a=>a.classList.remove('on')); el.classList.add('on'); document.getElementById('pageTitle').textContent=title; }
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
const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg, kind=''){ const el=document.createElement('div'); el.className='toast '+kind; el.textContent=msg; document.getElementById('toast').appendChild(el); setTimeout(()=>{el.style.opacity=0;el.style.transform='translateX(24px)';setTimeout(()=>el.remove(),350);},3600); }
function busy(btn, on, text){ if(!btn) return; btn.disabled=on; if(on){btn.dataset.old=btn.innerHTML; btn.innerHTML='<span class="spin"></span>'+text;} else if(btn.dataset.old){btn.innerHTML=btn.dataset.old;} }

const LABELS=[['polls','Polls'],['mirrored','Mirrored'],['filtered','Filtered'],['edited','Edits'],['failed','Failures'],['retries','Retries']];
const nums={};
function animate(el,to){ if(!el) return; const from=nums[el.id]||0; nums[el.id]=to; if(from===to){el.textContent=to;return;} const t0=performance.now();
  function step(t){const p=Math.min(1,(t-t0)/420); const e=1-Math.pow(1-p,3); el.textContent=Math.round(from+(to-from)*e); if(p<1)requestAnimationFrame(step);} requestAnimationFrame(step); }

async function refresh(){
  try{
    const [s,ev,cfg] = await Promise.all([api('/api/status'),api('/api/events?limit=60'),api('/api/config')]);
    const st=document.getElementById('state'); st.className='chip '+(s.state==='running'?'running':s.state==='error'?'error':'');
    st.innerHTML='<i class="dot"></i>'+esc(s.state);
    const md=document.getElementById('mode'); md.textContent=s.mode; md.className='chip ghost';
    document.getElementById('sub').textContent = s.error ? s.error : (s.state==='running' ? 'Running '+s.uptime_seconds+'s · private workspace' : 'Idle · your data is private');
    const grid=document.getElementById('stats');
    if(!grid.children.length) grid.innerHTML = LABELS.map(([k,l])=>'<div class="metric'+(k==='failed'?' is-err':'')+'" id="m_'+k+'"><div class="k">'+l+'</div><div class="n" id="n_'+k+'">0</div><div class="bar"><i id="b_'+k+'"></i></div></div>').join('');
    const c=s.stats.counters||{};
    const peak=Math.max(1, ...LABELS.map(([k])=>c[k]||0));
    LABELS.forEach(([k])=>{ animate(document.getElementById('n_'+k),c[k]||0); const bar=document.getElementById('b_'+k); if(bar) bar.style.width=((c[k]||0)/peak*100)+'%'; });
    const pos={}; (s.stats.sources||[]).forEach(x=>pos[x.key]=x.last_message_id);
    document.getElementById('groupsBody').innerHTML=(s.groups||[]).map(g=>{
      const last=g.sources.map(src=>{const k=Object.keys(pos).find(k=>k.endsWith('::'+src)); return k?pos[k]:'—';});
      return '<tr><td><b>'+esc(g.name)+'</b><div class="muted" style="font-size:10.5px;margin-top:3px">whitelist: '+(g.whitelist.length?esc(g.whitelist.join(', ')):'off')+'</div></td>'+
        '<td>'+g.sources.map(x=>'<span class="tag">'+esc(x)+'</span>').join('')+'</td>'+
        '<td>'+g.destinations.map(x=>'<span class="tag">'+esc(x)+'</span>').join('')+'</td>'+
        '<td class="mono" style="font-size:11px">'+esc(last.join(', '))+'</td></tr>';
    }).join('')||'<tr><td colspan="4" class="muted" style="font-size:12px">No groups configured</td></tr>';
    const ta=document.getElementById('yaml'); if(!ta.dataset.dirty) ta.value=cfg.redacted||'';
    document.getElementById('configPreview').textContent=cfg.redacted||'(empty — add your own config)';
    document.getElementById('events').innerHTML=(ev.events||[]).slice().reverse().map(e=>
      '<div class="ev '+esc(e.level)+'"><span class="t">'+esc((e.time||'').slice(11,19))+'</span><div class="body"><span class="k">'+esc(e.kind)+'</span><span class="tx">'+esc(e.message)+'</span></div></div>').join('')||'<div class="muted" style="font-size:12px">No events yet.</div>';
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
  document.getElementById('logoutBtn').style.display=guest?'none':'';
  document.getElementById('authBtn').style.display=guest?'':'none';
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
