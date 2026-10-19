// UI-only synthetic fixtures. Never loaded by the app or sent to a real backend.
import http from 'node:http';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const day='2026-10-16';
const tradeId='00000000-0000-4000-8000-000000000001';
const snapshot={snapshot_date:day,nav:1002000,cash:902000,market_value:100000,realized_pnl:2000,unrealized_pnl:0,open_risk:5000,portfolio_heat_pct:.5,positions:[]};
const signal={instrument_id:'NSE:TESTONLY',score:74,qualified:true,selected:true,components:{rally_quality:20,fundamental_strength:18},rejected_reasons:[],details:{kind:'SIGNAL',sector:'Test fixtures',buy_stop:100,stop:95,shares:10,rupee_risk:50,days_left:2,expires_on:'2026-10-20',reason:'Synthetic test fixture; never used in production',rank:1,global_blockers:[]}};
const run={id:tradeId,job_name:'daily_orchestrator',status:'BLOCKED',started_at:`${day}T19:00:00+05:30`,finished_at:`${day}T19:01:00+05:30`,details:{trade_date:day,reasons:['test_fixture_data_blocked'],stages:{paper_settlement:{status:'SUCCESS',finished_at:`${day}T19:01:00+05:30`},preflight:{status:'SUCCESS',finished_at:`${day}T19:01:00+05:30`}},paper_execution_status:'BLOCKED'}};
const overview={requested_date:day,market_session:day,decision_date:day,strategy_version:'TEST_ONLY',account_mode:'PAPER',portfolio:snapshot,nav_history:[{snapshot_date:'2026-10-15',nav:1000000},{snapshot_date:day,nav:1002000}],safety:{execution_mode:'PAPER',new_orders_enabled:false,emergency_stop:false,locked_reason:'test_fixture_data_blocked'},regime:{trade_date:day,band:'UNAVAILABLE',max_open_trades:0,score:0,raw_score:0,hard_override:false,data_ready:false,reason:'Synthetic fixture coverage unavailable',components:{},data_quality:{liquidity:{master_universe:2302,liquid_universe:69,unclassified:2050}}},daily_run:run,settlement:run,signals:{total:1,qualified:1,selected:1}};
const trade={id:tradeId,instrument:'NSE:TESTONLY',signal_date:day,state:'LEG1_ARMED',entry:100,stop:95,shares:10,remaining_shares:10,realized_pnl:0,exit_reason:null,updated_at:`${day}T19:00:00+05:30`};
const order={id:tradeId,strategy_trade_id:tradeId,instrument:'NSE:TESTONLY',side:'BUY',order_type:'STOP_MARKET',quantity:10,filled_quantity:10,status:'FILLED',created_at:`${day}T18:00:00+05:30`,trigger_price:100,limit_price:null};
const fill={id:tradeId,instrument:'NSE:TESTONLY',strategy_trade_id:tradeId,side:'BUY',quantity:10,price:100,fees:2,fill_time:`${day}T15:30:00+05:30`};
let unavailable=false,empty=false;const mutations=[];const errors=[];
const server=http.createServer(async(req,res)=>{const url=new URL(req.url,'http://localhost');const path=url.pathname;res.setHeader('content-type','application/json');if(unavailable){res.statusCode=503;res.end(JSON.stringify({detail:'Unavailable fixture'}));return;}if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;mutations.push({path,body:JSON.parse(body||'{}'),credential:req.headers['x-ops-key']});await new Promise(r=>setTimeout(r,200));res.end(JSON.stringify({order_id:tradeId,status:'OPEN',fill_count:0,changed:true}));return;}
let data={};
if(path==='/health')data={status:'ok',database:'configured',strategy_version:'TEST_ONLY'};
else if(path==='/v1/console/overview')data=empty?{...overview,portfolio:null,nav_history:[],daily_run:null,regime:null,safety:null,signals:{total:0,qualified:0,selected:0}}:overview;
else if(path==='/v1/console/signals')data={requested_date:day,decision_date:empty?null:day,page:Number(url.searchParams.get('page')||1),limit:50,total:empty?0:101,counts:{all:101,selected:1,watchlist:2,rejected:98},rows:empty||url.searchParams.get('q')==='NOTFOUND'?[]:[signal],proposal_only:true};
else if(path==='/v1/console/universe')data={as_of:day,page:1,limit:50,total:1,rows:empty?[]:[{instrument_id:'NSE:TESTONLY',sector:'Test fixtures',eligible:false,score:40,failed_rules:['test_only'],as_of:`${day}T19:00:00+05:30`}]};
else if(path==='/v1/console/paper')data={mode:'PAPER',market_session:day,snapshot:empty?null:snapshot,trades:empty?[]:[trade],orders:empty?[]:[order],fills:empty?[]:[fill],limit:100};
else if(path.startsWith('/v1/console/trades/'))data={mode:'PAPER',trade,events:[{id:1,event_time:`${day}T19:00:00+05:30`,state:'FILLED',event_type:'FILLED',payload:{entry:100,stop:95,shares:10}}],fills:[fill]};
else if(path==='/v1/console/data')data={market_session:day,metrics:{active_stocks:2302,classified_stocks:2302,stocks_with_session_bar:100,financial_filings:100,critical_errors:0},issues:[],benchmarks:[{benchmark_code:'INDIAVIX',rows:200,first_date:'2026-01-01',latest:day}],regime_data_quality:{liquidity:{liquid_universe:69,unclassified:2050}},coverage_note:'Row presence does not prove complete history.'};
else if(path.includes('/module-a/'))data={snapshot:{sales_yoy_pct:20,pat_yoy_pct:null,filing_timestamp:`${day}T10:00:00+05:30`,sector:'Test fixtures'},decision:{eligible:false,score:40,failed_rules:['test_only'],components:{growth:20},flags:[],triggers:[]}};
else if(path.includes('/module-b/'))data={eligible_universe:false,rejected_reasons:['module_a_ineligible'],signal:null,watchlist:null};
else if(path==='/v1/execution/paper-sandbox/account')data={account_mode:'PAPER_SANDBOX',starting_cash:1000000,cash:1000000,realized_pnl:0,fills:0,latest_market_date:day,orders:[{...order,status:'OPEN'}],positions:[]};
else if(path==='/v1/execution/readiness')data={stage:'PAPER',readiness_status:'NOT_READY',gates:{data_complete:false}};
else if(path==='/v1/execution/paper-validation/latest'||path==='/v1/execution/reconciliation/latest')data={status:'never_run'};
else if(path==='/v1/execution/workers')data={count:1,runs:[run]};
else if(path==='/v1/execution/audit')data={count:0,events:[]};
else if(path.startsWith('/v1/backtests/'))data={run:{id:tradeId,started_on:'2026-01-01',ended_on:day,status:'COMPLETED',initial_capital:1000000,realized_net_pnl:2000,return_pct:.2},fills:[]};
else data={count:0,runs:[]};
res.end(JSON.stringify(data));});
await new Promise(r=>server.listen(8019,'127.0.0.1',r));
const app=spawn(process.execPath,['node_modules/next/dist/bin/next','start','-p','3019'],{stdio:['ignore','pipe','pipe'],env:{...process.env,PMS_API_BASE_URL:'http://127.0.0.1:8019',OPS_API_KEY:'server-key-must-never-be-forwarded'}});let output='';app.stdout.on('data',c=>output+=c);app.stderr.on('data',c=>output+=c);
let browser;
try{for(let i=0;i<90;i++){try{await fetch('http://127.0.0.1:3019');break;}catch{if(i===89)throw new Error(output);await new Promise(r=>setTimeout(r,1000));}}
browser=await chromium.launch({headless:true});const context=await browser.newContext();const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
const routes=['/','/signals','/paper-trading','/regime','/research','/research/NSE%3ATESTONLY','/backtests','/data-health','/operations','/paper-trading/sandbox',`/paper-trading/${tradeId}`,`/backtests/${tradeId}`];
await mkdir('test-results',{recursive:true});
for(const route of routes){const response=await page.goto(`http://127.0.0.1:3019${route}`);assert.equal(response.status(),200,route);await page.locator('main h1').waitFor();assert(await page.locator('main h1').innerText(),route);}
assert.equal(mutations.length,0,'Read-only navigation performed a mutation');
await page.setViewportSize({width:1440,height:1000});await page.goto('http://127.0.0.1:3019');await page.screenshot({path:'test-results/overview-desktop.png',fullPage:true});
await page.goto('http://127.0.0.1:3019/signals');await page.getByRole('link',{name:'Watchlist',exact:true}).click();await page.waitForURL(u=>u.searchParams.get('view')==='watchlist');await page.locator('input[name=q]').fill('NOTFOUND');await page.getByRole('button',{name:'Apply',exact:true}).click();await page.getByText('No symbols match this search').waitFor();
await page.goto('http://127.0.0.1:3019/signals');await page.getByRole('link',{name:'Next',exact:true}).click();await page.waitForURL(u=>u.searchParams.get('page')==='2');
await page.goto('http://127.0.0.1:3019/paper-trading/sandbox');const form=page.locator('form').filter({has:page.getByRole('button',{name:'Create sandbox order',exact:true})});await form.locator('[name=symbol]').fill('TESTONLY');await form.locator('[name=order_type]').selectOption('LIMIT');await form.locator('[name=limit_price]').fill('100');await form.getByRole('button',{name:'Create sandbox order',exact:true}).click();assert.equal(mutations.length,0,'Missing operator key must block submission');await form.locator('[name=operator_key]').fill('ui-fixture-operator');await form.getByRole('button',{name:'Create sandbox order',exact:true}).click();await page.getByText('Sandbox order recorded.',{exact:false}).waitFor();assert.equal(mutations.length,1);assert.equal(mutations[0].credential,'ui-fixture-operator');assert.equal(mutations[0].body.instrument_id,'NSE:TESTONLY');assert(mutations[0].body.idempotency_key.startsWith('ui-sandbox:'));assert(!JSON.stringify(mutations).includes('server-key-must-never-be-forwarded'));
await page.setViewportSize({width:390,height:844});for(const route of ['/','/signals','/paper-trading','/operations']){await page.goto('http://127.0.0.1:3019'+route);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile overflow: '+route);}await page.goto('http://127.0.0.1:3019');await page.screenshot({path:'test-results/overview-mobile.png',fullPage:true});
empty=true;await page.goto('http://127.0.0.1:3019');await page.getByText('No daily run recorded').waitFor();await page.getByText('NAV history is not available yet').waitFor();
unavailable=true;await page.goto('http://127.0.0.1:3019');await page.getByRole('alert').waitFor();assert(await page.getByText('Unable to reach the data service.',{exact:false}).count()||await page.getByText('The data service is unavailable.',{exact:false}).count());await page.screenshot({path:'test-results/unavailable-mobile.png',fullPage:true});
assert.deepEqual(errors,[]);await writeFile('test-results/smoke.json',JSON.stringify({passed:true,routes:routes.length,checks:['desktop/mobile routes','date/search/status/pagination','formal/sandbox separation','operator-key requirement and no privileged proxy','empty and API-unavailable states','no hydration/page errors'],mutations:mutations.length},null,2));console.log('UI smoke passed: all routes, responsive layout, filters, authenticated sandbox submission, empty/error states.');
}finally{await browser?.close();app.kill('SIGTERM');await new Promise(r=>server.close(r));}
