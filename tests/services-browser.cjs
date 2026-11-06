const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),path=require('path');
const output=process.env.SCREENSHOT_DIR||path.join(process.cwd(),'results');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:960},acceptDownloads:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8000');
  await page.locator('#device-label').fill('Real laptop (browser test)');
  const pending=page.waitForResponse(r=>r.url().endsWith('/services/check'));
  await page.locator('#service-run').click();
  const response=await pending;if(response.status()!==200)throw Error(await response.text());
  const live=await response.json();
  await page.waitForFunction(()=>!document.getElementById('service-run').disabled);
  if(await page.locator('#service-stages .check').count()!==4)throw Error('Missing service stages');
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#service-export').click()]);
  const destination=path.join(output,'service-real-laptop.json');await download.saveAs(destination);
  const saved=JSON.parse(fs.readFileSync(destination));
  if(saved.report_id!==live.report_id||saved.mode!=='live')throw Error('Export mismatch');
  await page.locator('#add-report').click();
  await page.locator('#imports').setInputFiles(destination);
  await page.waitForFunction(()=>document.getElementById('fleet-status').textContent.includes('already'));
  await page.locator('#clear-reports').click();
  const examples=await (await page.request.get('http://127.0.0.1:8000/services/examples')).json();
  await page.locator('#add-report').click();
  await page.locator('#imports').setInputFiles(examples.slice(0,2).map((r,i)=>({name:`simulated-${i+1}.json`,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(r))})));
  await page.waitForFunction(()=>document.querySelectorAll('.report-item').length===3);
  await page.locator('#fleet-compare').click();
  await page.waitForFunction(()=>document.getElementById('fleet-status').textContent.startsWith('INCLUDES SIMULATED'));
  if(!(await page.locator('#fleet-output').innerText()).includes('passed on some'))throw Error('Imported mixed comparison missing');
  const [comparisonDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#fleet-export').click()]);
  const comparisonPath=path.join(output,'service-comparison-mixed-example.json');await comparisonDownload.saveAs(comparisonPath);
  const comparison=JSON.parse(fs.readFileSync(comparisonPath));
  if(!comparison.comparison.includes_simulation||comparison.reports.length!==3)throw Error('Comparison export lost provenance');
  await page.locator('#clear-reports').click();
  await page.locator('#demo-reports').click();
  await page.waitForFunction(()=>document.querySelectorAll('.report-item').length===3);
  await page.locator('#fleet-compare').click();
  await page.waitForFunction(()=>document.getElementById('fleet-status').textContent.startsWith('INCLUDES SIMULATED'));
  if(!(await page.locator('#fleet-output').innerText()).includes('matching symptom'))throw Error('No failure interpretation');
  await page.locator('#service-lab').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'services-demo-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  await page.screenshot({path:path.join(output,'services-demo-mobile.png'),fullPage:true});
  if(errors.length||overflow)throw Error(JSON.stringify({errors,overflow}));
  const report={mode:'One real laptop service check/export; synthetic multi-computer example clearly labeled in UI',
   real_target:live.target,real_stages:live.stages,service_export_verified:true,
   duplicate_import_rejected:true,valid_file_imports_verified:true,comparison_export_verified:true,simulation_label_verified:true,comparison_table_verified:true,
   mobile_horizontal_overflow:overflow,page_errors:errors,passed:true};
  fs.writeFileSync('results/services-browser.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
