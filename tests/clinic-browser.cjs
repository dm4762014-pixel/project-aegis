const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),path=require('path');
const output=process.env.SCREENSHOT_DIR || path.join(process.cwd(),'results');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:960},acceptDownloads:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const scans=[];page.on('response',async r=>{if(r.url().endsWith('/clinic/run')&&r.status()===200)scans.push(await r.json())});
  await page.goto('http://127.0.0.1:8000');
  await page.locator('#run').click();
  await page.waitForFunction(()=>!document.getElementById('run').disabled,{},{timeout:20000});
  if(await page.locator('#checks .check').count()!==5)throw Error('Missing check evidence');
  if(await page.locator('#error').innerText())throw Error('Diagnostic failed');
  await page.locator('#run').click();
  await page.waitForFunction(()=>!document.getElementById('run').disabled,{},{timeout:20000});
  if(await page.locator('#changes .compare-row').count()!==5)throw Error('Comparison missing');
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export').click()]);
  const exportPath=path.join(output,'connection-clinic-report.json');await download.saveAs(exportPath);
  const report=JSON.parse(fs.readFileSync(exportPath,'utf8'));
  if(report.current.mode!=='live'||report.previous.mode!=='live')throw Error('Expected real live runs');
  if(!report.comparison||report.current.checks.length!==5)throw Error('Invalid export');
  await page.screenshot({path:path.join(output,'connection-clinic-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  await page.screenshot({path:path.join(output,'connection-clinic-mobile.png'),fullPage:true});
  if(overflow||errors.length)throw Error(JSON.stringify({overflow,errors}));
  const evidence={mode:'Real Chromium and real network probes; no network mocks',live_runs:scans.length,
    check_count:5,export_contains_before_and_after:true,mobile_horizontal_overflow:overflow,page_errors:errors,
    result:report.current.finding,passed:true};
  fs.writeFileSync('results/clinic-browser.json',JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
