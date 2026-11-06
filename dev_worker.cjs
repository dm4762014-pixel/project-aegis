/* Real browser observations from owned local fixtures only. No response mocks. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),path=require('path');
const runId=process.argv[2];
if(!/^[a-f0-9-]{36}$/.test(runId||''))throw Error('A generated run ID is required.');
const snapshotDir=path.join(__dirname,'results','developer-captures',runId);
fs.mkdirSync(snapshotDir,{recursive:true});
const cases=['cors','route','server','html','runtime','healthy'];
(async()=>{
 const browser=await chromium.launch({headless:true});const captures=[];
 try{
  for(const scenario of cases){for(const variant of ['broken','fixed']){
   const frontend=`http://127.0.0.1:8000/dev/lab/${scenario}/${variant}`;
   const api=`http://127.0.0.1:8012/api/${scenario}/${variant}`;
   const context=await browser.newContext({viewport:{width:800,height:480},serviceWorkers:'block',acceptDownloads:false,
     recordHar:{path:path.join(snapshotDir,`${scenario}-${variant}.har`),mode:'minimal',content:'omit'}});
   try{
    await context.route('**/*',route=>{
     const r=route.request(),u=new URL(r.url());
     return ['http://127.0.0.1:8000','http://127.0.0.1:8012'].includes(u.origin)&&['GET','HEAD','OPTIONS'].includes(r.method())?route.continue():route.abort();
    });
    const page=await context.newPage();const requests=[],responseTasks=[];let cors=false,errors=0;
    page.on('popup',popup=>popup.close());
    page.on('pageerror',()=>errors++);
    page.on('console',m=>{if(/CORS policy|Cross-Origin Request Blocked/i.test(m.text()))cors=true});
    page.on('requestfailed',r=>{if(requests.length<100)requests.push({url:r.url(),method:r.method(),status:0,failed:true,resource_type:r.resourceType(),content_type:''})});
    page.on('response',r=>{responseTasks.push((async()=>{if(requests.length<100)requests.push({url:r.url(),method:r.request().method(),status:r.status(),failed:false,resource_type:r.request().resourceType(),content_type:(await r.headerValue('content-type')||'').split(';')[0]})})())});
    await page.goto(frontend,{waitUntil:'load',timeout:10000});
    await page.waitForFunction(()=>window.__clinicDone===true,{},{timeout:6000});
    await Promise.all(responseTasks);
    await page.screenshot({path:path.join(snapshotDir,`${scenario}-${variant}.png`)});
    const probe=await context.request.get(api,{timeout:5000,maxRedirects:0});
    captures.push({scenario,variant,observation:{source:'Real Chromium; owned local failure lab; no response mocks',
     frontend_url:frontend,api_url:api,requests,console_cors:cors,page_error_count:errors,
     app_state:await page.locator('#app').getAttribute('data-state'),
     direct_probe:{status:probe.status(),content_type:(probe.headers()['content-type']||'').split(';')[0]}}});
   }finally{await context.close()}
  }}
  process.stdout.write(JSON.stringify({captures}));
 }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exitCode=1});
