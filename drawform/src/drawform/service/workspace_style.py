"""Shared visual language for the opt-in, same-origin workspace shell."""
STYLE = r'''<style id="drawform-workspace-theme">
@font-face{font-family:"Douyin Sans UI";src:url("/assets/fonts/douyin/DouyinSans.woff2") format("woff2");font-weight:400 700;font-style:normal;font-display:swap}
@font-face{font-family:"YouShe Title Round";src:url("/assets/fonts/youshe/YouSheBiaoTiYuan.woff2") format("woff2");font-weight:400 700;font-style:normal;font-display:swap}
body[data-df-theme]{--mat:#6b879d;--paper:#fff;--panel:#f1f5f7;--line:#d8e2e9;--line-strong:#b9cbd7;--ink:#193341;--ink-2:#455d6c;--ink-3:#526b7b;--accent:#35586e;background:var(--mat);font-family:"Douyin Sans UI","PingFang SC","Microsoft YaHei",sans-serif;font-synthesis:none}
body[data-df-theme] .df-suite-nav{display:flex;align-items:center;gap:24px;flex:none;min-height:88px;padding:14px 28px;background:#35586e;color:#fff;border-bottom:1px solid #9eb2c0;position:relative;z-index:1}
body[data-df-theme] .df-suite-brand{display:flex;align-items:center;gap:12px;flex:none;color:#fff;text-decoration:none;white-space:nowrap}
body[data-df-theme] .df-saic-tile{display:grid;place-items:center;width:48px;height:48px;flex:none;border-radius:9px;background:#fff}
body[data-df-theme] .df-saic-tile img{display:block;width:32px;height:32px;object-fit:contain}
body[data-df-theme] .df-saic-caption{font-size:13px;letter-spacing:1px}
body[data-df-theme] .df-brand-divider{width:1px;height:32px;background:#8199aa;margin:0 3px}
body[data-df-theme] .df-brand-product{display:flex;flex-direction:column;gap:2px}
body[data-df-theme] .df-suite-wordmark{font-family:"YouShe Title Round","PingFang SC",sans-serif;font-weight:400;font-synthesis:none;font-size:32px;line-height:1.25}
body[data-df-theme] .df-brand-caption{font-size:10px;letter-spacing:1.6px;color:#e1eaf0}
body[data-df-theme] .df-suite-links{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-left:auto}
body[data-df-theme] .df-suite-links a{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:9px 13px;border:1px solid transparent;border-radius:7px;color:#fff;text-decoration:none;font-size:13px;white-space:nowrap}
body[data-df-theme] .df-suite-links a:hover{border-color:#95abba;background:#45677d}
body[data-df-theme] .df-suite-links a[aria-current=page],body[data-df-view=modeling]:has(input[name=input-mode][value=text]:checked) [data-df-link=text],body[data-df-view=modeling]:has(input[name=input-mode][value=drawing]:checked) [data-df-link=drawing]{background:#0066ff;border-color:#0066ff;color:#fff}
body[data-df-theme] a:focus-visible,body[data-df-theme] button:focus-visible,body[data-df-theme] input:focus-visible,body[data-df-theme] textarea:focus-visible,body[data-df-theme] select:focus-visible,body[data-df-theme] summary:focus-visible{outline:2px solid #193341;outline-offset:3px}
body[data-df-theme] .df-suite-nav a:focus-visible{outline-color:#0066ff}
body[data-df-theme] a:visited{color:#35586e}
body[data-df-theme] .df-suite-brand:visited,body[data-df-theme] .df-suite-links a:visited{color:#fff}
body[data-df-theme] .df-suite-links a[aria-current=page]:visited,body[data-df-view=modeling]:has(input[name=input-mode][value=text]:checked) [data-df-link=text]:visited,body[data-df-view=modeling]:has(input[name=input-mode][value=drawing]:checked) [data-df-link=drawing]:visited{color:#193341}
body[data-df-theme] .df-page-intro{position:relative;flex:none;padding:22px 28px 24px;color:white;border-bottom:1px solid #9eb2c0}
body[data-df-theme] .df-intro-label{font-size:11px;letter-spacing:2px;margin-bottom:9px;color:#f1f5f7}
body[data-df-theme] .df-intro-label span{display:inline-grid;place-items:center;width:20px;height:20px;background:#0066ff;color:#fff;border-radius:4px;margin-right:5px;font-size:15px}
body[data-df-theme] .df-page-intro h1{font-family:"YouShe Title Round","PingFang SC",sans-serif;font-size:34px;line-height:1.4;font-weight:400;font-synthesis:none;letter-spacing:0;margin:0 0 6px}
body[data-df-theme] .df-page-intro p{font-size:13px;line-height:1.7;margin:0;color:white}
body[data-df-theme] .df-local-label{position:absolute;right:28px;bottom:28px;border:1px solid #bdccd6;background:#58778e;padding:7px 12px;border-radius:6px;font-size:11px;letter-spacing:.5px}
body[data-df-view=modeling] .df-intro-drawing{display:none}
body[data-df-view=modeling]:has(input[name=input-mode][value=drawing]:checked) .df-intro-text{display:none}
body[data-df-view=modeling]:has(input[name=input-mode][value=drawing]:checked) .df-intro-drawing{display:block}
body[data-df-theme] button,body[data-df-theme] .btn,body[data-df-theme] .button,body[data-df-theme] input,body[data-df-theme] select,body[data-df-theme] textarea{border-radius:7px}
body[data-df-theme] button,body[data-df-theme] .btn,body[data-df-theme] .button{min-height:40px;height:auto;padding:8px 12px;line-height:1.5}
body[data-df-theme] button.primary,body[data-df-theme] .btn.primary{background:#0066ff;border-color:#0066ff;color:#fff;font-weight:650;min-height:44px}
body[data-df-theme] button.primary:hover,body[data-df-theme] .btn.primary:hover{background:#0055d6;border-color:#0047b3}
body[data-df-theme] button:disabled,body[data-df-theme] .btn:disabled{opacity:.45;cursor:not-allowed}
body[data-df-theme] input[type=text],body[data-df-theme] input[type=password],body[data-df-theme] input[type=search],body[data-df-theme] select{min-height:40px;height:40px;padding:8px 10px}
body[data-df-theme] textarea{padding:12px;line-height:1.8}
body[data-df-theme] input[type=file]{border:1px dashed #9bb3c2;background:#f2f6f9;border-radius:9px;padding:12px;line-height:1.7}
body[data-df-theme] input[type=file]::file-selector-button{padding:8px 10px;border:1px solid #bacbd6;border-radius:5px;background:#fff;color:#193341;margin-right:8px}
body[data-df-theme] .feedback{font-size:12px}
body[data-df-theme] .badge{border-radius:5px}
body[data-df-theme] .mono,body[data-df-theme] pre{overflow-wrap:anywhere}
body[data-df-theme] footer{background:#35586e;color:#edf3f6;border-top:1px solid #a3b8c5;padding:12px 24px;font-size:11px}
/* Native input, model preview, clarification and delivery controls stay in order. */
body[data-df-theme=modeling]>header{display:none}
body[data-df-theme=modeling] .layout{grid-template-columns:340px minmax(0,1fr);min-height:calc(100vh - 248px);gap:18px;padding:20px 24px 28px;align-items:start}
body[data-df-theme=modeling] .sidebar{min-width:0;background:transparent;border:0;padding:0}
body[data-df-theme=modeling] .sidebar>section,body[data-df-theme=modeling] .df-input-extra,body[data-df-theme=modeling] .df-sidebar-extra{border:1px solid #dae4eb;border-radius:13px;background:#fff;padding:18px;margin:0 0 14px;box-shadow:0 5px 18px #263d4c0c}
body[data-df-theme=modeling] .sidebar section+section{padding-top:18px;margin-top:0;border-top:1px solid #dae4eb}
body[data-df-theme=modeling] .sidebar h2{font-size:15px;line-height:1.6}
body[data-df-theme=modeling] .sidebar .lead{font-size:12px;margin:7px 0 14px}
body[data-df-theme=modeling] .sidebar .field{font-size:12px;margin-top:14px}
body[data-df-theme=modeling] #connection-form .row{flex-wrap:nowrap}
body[data-df-theme=modeling] #connection-form input{min-width:0}
body[data-df-theme=modeling] #connect{flex:none}
body[data-df-theme=modeling] .input-modes{gap:6px;flex-wrap:wrap}
body[data-df-theme=modeling] .input-modes legend{margin-bottom:8px}
body[data-df-theme=modeling] .input-modes label{flex:1;justify-content:center;min-height:40px;border:1px solid #c8d7e1;background:#f2f6f9;border-radius:6px;padding:6px;font-size:12px;cursor:pointer}
body[data-df-theme=modeling] .input-modes label:has(:checked){border-color:#35586e;background:#e4edf3;color:#193341;font-weight:600}
body[data-df-theme=modeling] .input-modes input{accent-color:#35586e}
body[data-df-theme=modeling] .example-row{gap:6px;margin:10px 0}
body[data-df-theme=modeling] .example-row button{flex:1 1 calc(50% - 6px);min-height:36px;padding:6px 8px;text-align:left;justify-content:flex-start;font-size:11px;background:#f3f6f8;border-color:#d9e3ea}
body[data-df-theme=modeling] .scope{border-top:1px solid #dae4eb;padding-top:13px;line-height:1.8;font-size:11px}
body[data-df-theme=modeling] .workspace{padding:0;min-width:0}
body[data-df-theme=modeling] .steps{border:1px solid #c3d2dc;border-radius:10px;background:#f8fafb;margin:0 0 16px;box-shadow:0 3px 12px #1933410a}
body[data-df-theme=modeling] .steps li{font-size:12px;padding:12px 14px}
body[data-df-theme=modeling] .steps li[aria-current=step]{background:#0066ff;color:#fff}
body[data-df-theme=modeling] .work-surface{border:1px solid #d9e3e9;border-radius:13px;overflow:hidden;box-shadow:0 6px 22px #263d4c0e}
body[data-df-theme=modeling] .surface-head{min-height:56px;padding:14px 18px;background:#f1f5f7;border-bottom-color:#d6e1e8;flex-wrap:wrap}
body[data-df-theme=modeling] .surface-head h2{font-size:16px}
body[data-df-theme=modeling] .viewer{height:400px;background:radial-gradient(ellipse at 50% 30%,#fff,#edf3f6)}
body[data-df-theme=modeling] .viewer-empty strong{font-size:17px;margin-bottom:10px}
body[data-df-theme=modeling] .viewer-caption{background:#fbfcfd;padding:12px 18px}
body[data-df-theme=modeling] .viewer-caption button{min-height:36px;padding:6px 10px}
body[data-df-theme=modeling] .model-details,body[data-df-theme=modeling] .drawing-body,body[data-df-theme=modeling] .source-body,body[data-df-theme=modeling] .clarification-body,body[data-df-theme=modeling] .version-body{padding:18px}
body[data-df-theme=modeling] .params{gap:12px}
body[data-df-theme=modeling] .params>div{border:1px solid #dae4eb;background:#f4f7f9;border-radius:8px;padding:12px}
body[data-df-theme=modeling] .params dd{font-size:22px;margin-top:4px}
body[data-df-theme=modeling] .actions{gap:8px}
body[data-df-theme=modeling] .actions>button{white-space:normal}
body[data-df-theme=modeling] .drawing-preview,body[data-df-theme=modeling] .source-preview{border-radius:8px;overflow:hidden;background:#fff}
body[data-df-theme=modeling] .adoption-label{padding:12px;border:1px solid #d2e0e9;border-radius:8px;background:#f1f6f9;line-height:1.7}
body[data-df-theme=modeling] .adoption-label input{accent-color:#35586e;flex:none}
body[data-df-theme=modeling] .df-model-meta,body[data-df-theme=modeling] .df-source-context{border:1px solid #d4e0e8;border-radius:12px;background:#f4f7f9;padding:0;margin:0 0 16px;overflow:hidden}
body[data-df-theme=modeling] .df-model-meta>summary,body[data-df-theme=modeling] .df-source-context>summary,body[data-df-theme=modeling] .df-input-extra>summary,body[data-df-theme=modeling] .df-sidebar-extra>summary{padding:14px 17px;color:#193341;font-weight:600;min-height:48px;cursor:pointer}
body[data-df-theme=modeling] summary>span{font-weight:400;font-size:11px;color:#526b7b;margin-left:12px}
body[data-df-theme=modeling] .df-meta-grid{display:grid;grid-template-columns:1fr;gap:12px;padding:0 12px 12px}
body[data-df-theme=modeling] .df-model-meta .version-section,body[data-df-theme=modeling] .df-source-context .source-section{margin:0}
body[data-df-theme=modeling] .df-source-context>div{padding:0 12px 12px}
body[data-df-theme=modeling] .df-model-meta:not(:has(#version-section:not([hidden]),#project-section:not([hidden]))),body[data-df-theme=modeling] .df-source-context:has(#source-section[hidden]){display:none}
body[data-df-theme=modeling] .df-input-extra,body[data-df-theme=modeling] .df-sidebar-extra{padding:0}
body[data-df-theme=modeling] .df-input-extra>div>section,body[data-df-theme=modeling] .df-sidebar-extra>div>section{padding:0 18px 18px}
body[data-df-theme=modeling] .df-sidebar-extra h2{font-size:13px}
body[data-df-theme=modeling] .sidebar>section:has(.df-connection-settings){padding:10px 18px}
body[data-df-theme=modeling] .df-connection-settings{margin:0;border:0;padding:0}
body[data-df-theme=modeling] .df-connection-settings>summary{padding:3px 0;min-height:30px;color:#193341;font-weight:600}
body[data-df-theme=modeling] .df-connection-settings[open]>div{padding-top:10px}
body[data-df-theme=modeling] #connection-status{margin-top:5px}
body[data-df-view=projects] .steps{background:#f1f5f7}
body[data-df-view=projects] #projects-title{color:#35586e}
/* SVG, annotations, viewport and all review controls retain their native parents. */
body[data-df-theme=step] #top{height:auto;min-height:46px;padding:8px 24px;background:#45677d;border-color:#a5b8c6;color:white;gap:12px}
body[data-df-theme=step] #top h1,body[data-df-theme=step] #top .sep{display:none}
body[data-df-theme=step] #part,body[data-df-theme=step] #state{color:white;font-size:12px}
body[data-df-theme=step] #version-label{background:#f0f5f8;color:#193341;border-color:#bdccd6}
body[data-df-theme=step] #main{min-height:0}
body[data-df-theme=step] #side{width:350px;background:#e8eff4;padding:14px 14px 20px;border-color:#a8bfce}
body[data-df-theme=step] #side>section{background:white;border:1px solid #d3dfe7;border-radius:11px;padding:0 14px;margin-bottom:12px}
body[data-df-theme=step] .section-toggle{padding:15px 0 12px;color:#193341;font-size:13px}
body[data-df-theme=step] #reference-actions{background:#fff;border-color:#d4e0e7}
body[data-df-theme=step] #stage{background:#6b879d;padding:18px}
body[data-df-theme=step] #paper{background:#fff;color:#193341;box-shadow:0 8px 28px #19334138}
body[data-df-theme=step] #zoominfo,body[data-df-theme=step] #zoom-hint,body[data-df-theme=step] #task-title,body[data-df-theme=step] #task-draft-status,body[data-df-theme=step] #quick-downloads>.hint{color:#fff}
body[data-df-theme=step] #verbar{border-radius:8px;border-color:#d3dfe7}
body[data-df-theme=step] #verbar>button{min-height:40px;padding:8px 12px;border-radius:0}
body[data-df-theme=step] #verbar>button.on{background:#0066ff;color:#fff}
body[data-df-theme=step] .dl{min-height:36px;padding:7px 12px;border-radius:7px;background:#f3f7f9;border-color:#d2dee6;color:#193341}
body[data-df-theme=step] #state-notice,body[data-df-theme=step] #history-notice,body[data-df-theme=step] #draft-banner{border-radius:8px}
body[data-df-theme=step] #workspace{width:min(1240px,100%);margin:0 auto 24px;display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);gap:18px}
body[data-df-theme=step] #workspace>h2,body[data-df-theme=step] #workspace>.workspace-lead,body[data-df-theme=step] #workspace>.flow-steps{display:none}
body[data-df-theme=step] .workspace-card{padding:24px;border:1px solid #d7e3eb;border-radius:14px;margin:0;box-shadow:0 6px 20px #1933410d}
body[data-df-theme=step] .workspace-card h3{font-size:17px;margin:0 0 14px}
body[data-df-theme=step] #connection-card{grid-column:1/-1}
body[data-df-theme=step] #up{margin:10px 0 14px}
body[data-df-theme=step] #job-tools{gap:8px}
body[data-df-theme=step] #job-search{min-width:0;flex:1 1 180px}
body[data-df-theme=step] #job-filter{flex:0 1 150px}
body[data-df-theme=step] #jobs{grid-template-columns:1fr;gap:8px;margin-bottom:0}
body[data-df-theme=step] #jobs button{min-height:78px;padding:13px 15px;border-radius:9px;border-color:#d6e1e9;background:#f6f9fb}
body[data-df-theme=step] #jobs button:hover{background:#e9f0f5;border-color:#9ab1c0}
body[data-df-theme=step] .job-meta{overflow-wrap:anywhere}
body[data-df-theme=step][data-screen=task] #workspace{display:none}
body[data-df-theme=step][data-screen=task] .df-page-intro{padding:12px 24px 14px}
body[data-df-theme=step][data-screen=task] .df-intro-label,body[data-df-theme=step][data-screen=task] .df-local-label{display:none}
body[data-df-theme=step][data-screen=task] .df-page-intro h1{font-size:26px;margin:0}
body[data-df-theme=step][data-screen=task] .df-page-intro p{font-size:11px;margin-top:2px}
@media(min-width:1280px){body[data-df-view=projects] .df-meta-grid{grid-template-columns:minmax(280px,.8fr) minmax(0,1.2fr)}}
@media(min-width:1600px){body[data-df-theme=modeling] .layout{grid-template-columns:370px minmax(0,1fr);gap:24px;padding:24px 30px}body[data-df-theme=modeling] .viewer{height:460px}}
@media(max-width:1100px){body[data-df-theme] .df-suite-nav{padding:14px 20px;gap:14px}body[data-df-theme] .df-saic-caption{display:none}body[data-df-theme] .df-suite-links a{padding:8px 9px;font-size:12px}body[data-df-theme=modeling] .layout{grid-template-columns:305px minmax(0,1fr);gap:14px;padding:16px}body[data-df-theme=modeling] .steps li{padding:11px 8px}body[data-df-theme=modeling] .params{grid-template-columns:repeat(2,minmax(0,1fr))}body[data-df-theme=modeling] .viewer{height:350px}}
@media(max-width:850px){body[data-df-theme] .df-suite-nav{flex-wrap:wrap;gap:12px}body[data-df-theme] .df-suite-links{margin-left:0;width:100%;gap:4px}body[data-df-theme] .df-suite-links a{flex:1;padding:8px 6px}body[data-df-theme] .df-local-label{display:none}body[data-df-theme=step] #workspace{grid-template-columns:1fr}body[data-df-theme=step] #connection-card{grid-column:auto}}
@media(max-width:760px){html:has(body[data-df-theme=step]){height:auto;min-height:100%}body[data-df-theme=step]{height:auto;min-height:100vh;overflow:auto}body[data-df-theme=step] #main{flex:none;min-height:0}body[data-df-theme=step] #side{width:100%;height:auto;max-height:none;overflow:visible;padding:14px;border-right:0}body[data-df-theme=step] #stage{flex:none;overflow:auto;min-height:280px;padding:14px}body[data-df-theme=step][data-screen=workspace] #stage{min-height:0}body[data-df-theme=step] #top{padding:8px 14px}body[data-df-theme=step] #part{max-width:100%;white-space:normal;overflow-wrap:anywhere}body[data-df-theme=step] #state{flex-basis:50%}body[data-df-theme=step] #reference-actions{position:static}body[data-df-theme] .df-page-intro{padding:20px}body[data-df-theme] .df-page-intro h1{font-size:30px}body[data-df-theme=modeling] .layout{display:block;padding:14px;min-height:0}body[data-df-theme=modeling] .workspace{padding:0;margin-top:18px}body[data-df-theme=modeling] .viewer{height:320px}body[data-df-theme=modeling] .fact-columns{grid-template-columns:1fr;gap:8px}body[data-df-theme=modeling] .revision-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:480px){body[data-df-theme] .df-suite-nav{padding:12px 14px;gap:12px}body[data-df-theme] .df-saic-tile{width:44px;height:44px;border-radius:8px}body[data-df-theme] .df-saic-tile img{width:28px;height:28px}body[data-df-theme] .df-brand-divider{height:28px;margin:0 1px}body[data-df-theme] .df-suite-wordmark{font-size:28px}body[data-df-theme] .df-saic-caption,body[data-df-theme] .df-brand-caption{display:none}body[data-df-theme] .df-suite-links{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}body[data-df-theme] [data-df-link=overview]{display:none}body[data-df-theme] .df-suite-links a{font-size:11px;padding:8px 3px;border-color:#698698;white-space:normal;text-align:center}body[data-df-theme] .df-page-intro{padding:18px 14px 20px}body[data-df-theme] .df-page-intro p{font-size:12px}body[data-df-theme=modeling] .sidebar>section{padding:16px}body[data-df-theme=modeling] .df-model-meta>summary,body[data-df-theme=modeling] .df-source-context>summary{padding:13px 12px}body[data-df-theme=modeling] summary>span{display:block;margin:4px 0 0 18px}body[data-df-theme=modeling] .df-meta-grid,body[data-df-theme=modeling] .df-source-context>div{padding:0 8px 8px}body[data-df-theme=modeling] .surface-head{padding:12px}body[data-df-theme=modeling] .model-details,body[data-df-theme=modeling] .drawing-body,body[data-df-theme=modeling] .source-body,body[data-df-theme=modeling] .clarification-body,body[data-df-theme=modeling] .version-body{padding:14px 12px}body[data-df-theme=modeling] .version-tools label{min-width:0;flex-basis:100%}body[data-df-theme=modeling] .actions>button{flex:1 1 100%}body[data-df-theme=modeling] .params{gap:8px}body[data-df-theme=modeling] .params>div{padding:10px}body[data-df-theme=modeling] .params dd{font-size:19px}body[data-df-theme=step] .workspace-card{padding:18px}body[data-df-theme=step] #connection-card.connected #connection-state{min-width:0;flex-basis:100%}body[data-df-theme=step] #verbar .vtag{overflow-wrap:anywhere}body[data-df-theme] footer{padding:12px 14px}}
@media(prefers-reduced-motion:reduce){body[data-df-theme] *,body[data-df-theme] *::before,body[data-df-theme] *::after{scroll-behavior:auto!important}}
@media(max-width:480px){body[data-df-theme] .df-suite-links [data-df-link=overview]{display:none}}
body[data-df-theme] button:not(:disabled):hover,body[data-df-theme] .button:hover{border-color:#0066ff;background:#edf4ff;color:#0051cc}
body[data-df-theme] button.primary:not(:disabled):hover,body[data-df-theme] .btn.primary:not(:disabled):hover{background:#0055d6;border-color:#0055d6;color:#fff}
body[data-df-theme] button:not(:disabled):active{background:#dbe9ff}
body[data-df-theme] button.primary:not(:disabled):active{background:#0047b3;color:#fff}
body[data-df-theme] summary:hover{background:#eaf2ff;color:#0051cc}
body[data-df-theme] .df-suite-links a:hover{background:#0066ff;border-color:#0066ff;color:#fff}
body[data-df-theme] .df-suite-links a[aria-current=page]:visited,body[data-df-view=modeling]:has(input[name=input-mode][value=text]:checked) [data-df-link=text]:visited,body[data-df-view=modeling]:has(input[name=input-mode][value=drawing]:checked) [data-df-link=drawing]:visited{color:#fff}
body[data-df-theme] input,body[data-df-theme] textarea,body[data-df-theme] select{accent-color:#0066ff}
body[data-df-theme] input:focus-visible,body[data-df-theme] textarea:focus-visible,body[data-df-theme] select:focus-visible,body[data-df-theme] button:focus-visible,body[data-df-theme] summary:focus-visible{outline-color:#0066ff}
body[data-df-theme=modeling] .input-modes label:has(:checked){background:#0066ff;border-color:#0066ff;color:#fff}
body[data-df-theme=modeling] .input-modes input{accent-color:#0066ff}
body[data-df-theme=modeling] .sidebar>section,body[data-df-theme=modeling] .work-surface{transition:box-shadow 150ms,border-color 150ms}
body[data-df-theme=modeling] .sidebar>section:focus-within,body[data-df-theme=modeling] .work-surface:focus-within{border-color:#7eacfa;box-shadow:0 0 0 2px #0066ff14}
@media(prefers-reduced-motion:reduce){body[data-df-theme=modeling] .sidebar>section,body[data-df-theme=modeling] .work-surface{transition:none}}
body[data-df-theme] button:disabled:hover{background:#fff;border-color:var(--line-strong);color:var(--ink)}
body[data-df-theme] button.primary:disabled:hover,body[data-df-theme] .btn.primary:disabled:hover{background:#0066ff;border-color:#0066ff;color:#fff}
</style>'''
