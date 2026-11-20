/* Behavior checks against the actual shared core and production portal painter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const core = require('../assets/js/cs-chart-presentation.js');
const def = {version:2,title:'Water',mode:'numeric',chart:'line',axes:{},presentation:{palette:'accessible',series:{},legend:{display:'show',position:'right',title:'Sites'},numberFormat:{decimals:2,useGrouping:false},axisTitles:{x:'Date',y:'cm'},referenceBands:[]}};
const keyA='s:0000000000000001', keyB='s:0000000000000002';
const payload={definition_version:2,category_keys:[],mode:'numeric',series:[{key:keyA,name:'A',xy:[{x:'2026-01-01',y:2},{x:'2026-02-01',y:1000}]},{key:keyB,name:'B',xy:[{x:'2026-01-01',y:3}]}],labels:[],y_max:10};
assert.deepEqual(core.resolveStyle(def,keyA,'A',0),core.resolveStyle(def,keyA,'A',7),'filter order must not recolor sites');
assert.equal(core.formatNumber(12.3,def,'en'),'12.30');
const cfg=core.buildConfig(def,payload,'en');
assert.equal(cfg.options.scales.y.max,undefined,'v2 must keep extreme values visible');
assert.equal(cfg.options.plugins.legend.display,false,'HTML legend replaces inaccessible canvas labels');
assert.equal(cfg.data.datasets[0].tension,0,'do not invent smooth measurements');
const limited=structuredClone(def);limited.axes={yMax:5};
assert.equal(core.outOfRangeCount(limited,payload),1);
assert.equal(core.buildConfig(limited,payload).options.scales.y.max,5);
assert.equal(core.buildConfig(limited,payload).options.scales.x.max,Date.parse('2026-02-01'),'manual Y clipping must preserve the full X timeline');
const bandDefinition=structuredClone(def);bandDefinition.presentation.referenceBands=[{id:'ref',min:-30,max:2000,color:'#0072B2',label:'Reference'}];
assert.equal(core.buildConfig(bandDefinition,payload).options.scales.y.suggestedMin,-30);
assert.equal(core.buildConfig(bandDefinition,payload).options.scales.y.suggestedMax,2000);
bandDefinition.axes={yMax:50};assert.equal(core.buildConfig(bandDefinition,payload).options.scales.y.suggestedMax,undefined);

const category=structuredClone(def);category.mode='category';category.chart='bar';category.axes={xMin:500};
const categorical={definition_version:2,mode:'category',labels:['One','Two'],category_keys:['c:0000000000000001','c:0000000000000002'],series:[{key:'s:0000000000000003',name:'Water',points:[4,8]}]};
assert.equal(core.outOfRangeCount(category,categorical),0);
assert.equal(core.buildConfig(category,categorical).options.scales.x.min,undefined);
category.chart='pie';category.axes.yMax=1;
assert.equal(core.outOfRangeCount(category,categorical),0);
assert.equal(core.buildConfig(def,payload,'en','dark').options.scales.y.ticks.color,'#e6eaef');
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.style={};this.events={};this.className='';this.dataset={};this.classList={add:(name)=>{this.className+=' '+name;},remove:()=>{}};}
  appendChild(node){if(node.parentNode)node.parentNode.removeChild(node);this.children.push(node);node.parentNode=this;return node;}
  insertBefore(node,before){if(node.parentNode)node.parentNode.removeChild(node);let i=this.children.indexOf(before);if(i<0)i=this.children.length;this.children.splice(i,0,node);node.parentNode=this;return node;}
  removeChild(node){this.children.splice(this.children.indexOf(node),1);node.parentNode=null;}
  get firstChild(){return this.children[0]||null;}
  get nextSibling(){return this.parentNode?.children[this.parentNode.children.indexOf(this)+1]||null;}
  setAttribute(key,value){this.attrs[key]=value;}
  getAttribute(key){return this.attrs[key]??null;}
  addEventListener(key,fn){this.events[key]=fn;}
  querySelector(selector){return walk(this).find(n=>n.className.split(' ').includes(selector.slice(1)))||null;}
}
function walk(node){return node.children.flatMap(n=>[n,...walk(n)]);}
const charts=[];
function Chart(_canvas,config){this.config=config;this.data=config.data;this.hidden={};this.update=()=>{};this.destroy=()=>{};this.setDatasetVisibility=(i,value)=>{this.hidden[i]=!value;};this.toggleDataVisibility=(i)=>{this.hidden[i]=!this.hidden[i];};charts.push(this);}
const sandbox={Chart,CSChartPresentation:core,document:{readyState:'complete',documentElement:{lang:'en'},querySelectorAll:()=>[],createElement:tag=>new Node(tag)},console};sandbox.window=sandbox;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-chart-explorer.js'),'utf8'),sandbox);
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-charts.js'),'utf8'),sandbox);
function paint(d,p){const figure=new Node('figure'),container=new Node('div');figure.appendChild(container);container.setAttribute('data-definition',JSON.stringify(d));container.setAttribute('data-type',d.chart);assert.equal(sandbox.csunescoCharts.paint(container,p),true);return {figure,chart:charts.at(-1)};}
function legendButtons(figure){const legend=walk(figure).find(n=>n.className==='cs-chart-studio-legend');return legend?walk(legend).filter(n=>n.tagName==='button'):[];}
const numeric=paint(limited,payload);let buttons=legendButtons(numeric.figure);
assert.equal(buttons.length,2);assert.equal(buttons[0].attrs['aria-pressed'],'true');buttons[0].events.click();buttons=legendButtons(numeric.figure);assert.equal(buttons[0].attrs['aria-pressed'],'false');assert.equal(charts.at(-1).data.datasets.length,1);buttons[0].events.click();assert.equal(charts.at(-1).data.datasets.length,2);
assert.ok(walk(numeric.figure).some(n=>n.className==='cs-chart-range-warning'));
const details=walk(numeric.figure).find(n=>n.className==='cs-chart-data-table');details.open=true;details.events.toggle();assert.ok(walk(details).some(n=>n.tagName==='td'&&n.textContent==='1000.00'),'alternative table retains clipped observations');
category.chart='bar';category.axes={};const bars=paint(category,categorical);legendButtons(bars.figure)[1].events.click();assert.equal(JSON.stringify(charts.at(-1).data.datasets[0].data),'[4]');legendButtons(bars.figure)[1].events.click();assert.equal(JSON.stringify(charts.at(-1).data.datasets[0].data),'[4,8]');assert.equal(categorical.series[0].points[1],8,'legend toggles are transient');
const legacy=structuredClone(def);legacy.version=1;const old=paint(legacy,payload);assert.equal(old.chart.config.options.scales.y.max,10,'v1 rendering is unchanged');

// Bucketed series use Chart.js category indices for tick positions; render
// the corresponding API period for both built-in and configured chart blocks.
const months = ['2026-09', '2026-10', '2026-11'];
for (const type of ['bar', 'line']) {
  const figure = new Node('figure'), container = new Node('div');
  figure.appendChild(container);
  container.setAttribute('data-type', type);
  const monthly = {mode: 'numeric', labels: months, series: [{name: 'Clarity', points: [2, 4, 3]}]};
  assert.equal(sandbox.csunescoCharts.paint(container, monthly), true);
  const axis = charts.at(-1).config.options.scales.x;
  assert.equal(axis.type, 'category');
  const scale = {getLabelForValue: value => months[value]};
  assert.deepEqual(months.map((_, i) => axis.ticks.callback.call(scale, i)), months);
}
const linear = old.chart.config.options.scales.x;
assert.equal(linear.type, 'linear');
const epoch = Date.parse('2026-09-01');
assert.equal(linear.ticks.callback(epoch), new Date(epoch).toLocaleDateString(undefined, {timeZone: 'UTC'}));

async function testCalculationRequestRetainsLocalPresentation(version, resolved=false) {
  const saved=structuredClone(def);saved.version=version;saved.title='Date(UTC)';saved.caption='Presentation-only description';saved.axes={yMax:20};saved.start='2026-01-01';saved.filters={sites:['A'],facets:{weather:['dry']},search:'river'};
  if(version===2){saved.presentation.series[keyA]={color:'#123456',label:'Upstream',lineStyle:'dashed',pointStyle:'triangle'};saved.template={id:'site-comparison',version:1};}
  else delete saved.presentation;
  if(resolved){delete saved.filters;delete saved.start;}
  const figure=new Node('figure'),container=new Node('div');figure.appendChild(container);
  container.setAttribute('data-definition',JSON.stringify(saved));container.setAttribute('data-type','line');container.setAttribute('data-series-url','/citizen-science/data/source/series');
  if(resolved){container.setAttribute('data-server-resolved','true');container.setAttribute('data-series-url','/citizen-science/project/river/charts/block?revision=4');container.setAttribute('data-mode','numeric');}
  const history=[{id:'historical-source',mappings:{old_value:'value'}}];container.setAttribute('data-history-sources',JSON.stringify(history));
  let sent,requests=0;const chartCount=charts.length;
  const responsePayload=structuredClone(payload);if(version===2){responsePayload.series[1].name='';delete responsePayload.title;}
  const networkSandbox={Chart,CSChartPresentation:core,document:{readyState:'complete',documentElement:{lang:'en'},querySelectorAll:()=>[container],createElement:tag=>new Node(tag)},fetch:(url,options)=>{requests++;sent={url,options};return Promise.resolve({ok:true,json:()=>Promise.resolve(responsePayload)});},console};networkSandbox.window=networkSandbox;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-chart-explorer.js'),'utf8'),networkSandbox);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-charts.js'),'utf8'),networkSandbox);
  await new Promise(resolve=>setImmediate(resolve));
  const expected=structuredClone(saved);if(version===2){delete expected.presentation;delete expected.template;delete expected.title;delete expected.caption;}
  if(resolved){assert.equal(sent.options.method,'GET');assert.equal(sent.options.credentials,'omit');assert.equal(sent.url,'/citizen-science/project/river/charts/block?revision=4');assert.equal(sent.options.body,undefined);assert.equal(sent.options.headers,undefined);}
  else {assert.deepEqual(JSON.parse(sent.options.body),{definition:expected,history_sources:history},'v2 sends calculation fields and history only; v1 sends the entire definition');assert.equal(sent.options.method,'POST');}
  assert.equal(charts.length,chartCount+1,'response still renders');
  assert.deepEqual(JSON.parse(container.getAttribute('data-definition')),saved,'full published definition is not modified');
  assert.equal(JSON.parse(container.getAttribute('data-definition')).title,'Date(UTC)','chart title remains available for local display');
  if(version===2){assert.equal(charts.at(-1).data.datasets[1].label,'Date(UTC)','unnamed series use the title retained locally');assert.equal(charts.at(-1).data.datasets[0].borderColor,'#123456');assert.equal(charts.at(-1).data.datasets[0].label,'Upstream');assert.deepEqual(charts.at(-1).data.datasets[0].borderDash,[7,4]);
    const controls=walk(figure).filter(n=>n.tagName==='select');controls[0].value='bar';controls[0].events.change();assert.equal(charts.at(-1).config.type,'bar');assert.ok(walk(figure).find(n=>n.tagName==='canvas').attrs['aria-label'].startsWith('bar chart'));controls[1].value='mono';controls[1].events.change();assert.notEqual(charts.at(-1).data.datasets[0].borderColor,'#123456');
    const hide=walk(figure).find(n=>n.tagName==='button'&&n.textContent==='Hide all');hide.events.click();assert.equal(charts.at(-1).data.datasets.length,0);assert.equal(walk(figure).find(n=>n.className==='cs-chart-explorer-empty').hidden,false);
    const rangeInputs=walk(figure).filter(n=>n.tagName==='input'&&n.type==='number');rangeInputs[0].value='10';rangeInputs[0].events.input();const lastValid=charts.length;rangeInputs[1].value='5';rangeInputs[1].events.input();assert.equal(charts.length,lastValid,'invalid ranges keep the previous chart');assert.equal(walk(figure).find(n=>n.className==='cs-chart-explorer-error').hidden,false);rangeInputs[1].value='50';rangeInputs[1].events.input();assert.equal(walk(figure).find(n=>n.className==='cs-chart-explorer-error').hidden,true);
    const reset=walk(figure).find(n=>n.tagName==='button'&&n.textContent==='Restore published view');reset.events.click();assert.equal(charts.at(-1).config.type,'line');assert.equal(charts.at(-1).data.datasets[0].borderColor,'#123456');assert.equal(charts.at(-1).data.datasets.length,2);assert.equal(requests,1,'visitor display changes never refetch data');
    assert.deepEqual(JSON.parse(container.getAttribute('data-definition')),saved);
  }
}
async function testUnavailableResolvedFeedDoesNotFetchPage(){
  const figure=new Node('figure'),container=new Node('div');figure.appendChild(container);container.setAttribute('data-server-resolved','true');container.setAttribute('data-series-url','');container.setAttribute('data-label-error','Chart unavailable');let requests=0;
  const context={Chart,CSChartPresentation:core,document:{readyState:'complete',documentElement:{lang:'en'},querySelectorAll:()=>[container],createElement:tag=>new Node(tag)},fetch:()=>{requests++;throw new Error('Must not fetch current page');},console};context.window=context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-charts.js'),'utf8'),context);await new Promise(resolve=>setImmediate(resolve));assert.equal(requests,0);assert.equal(container.attrs['aria-busy'],'false');assert.equal(container.querySelector('.cs-chart-message').textContent,'Chart unavailable');
}
Promise.resolve().then(()=>testUnavailableResolvedFeedDoesNotFetchPage()).then(()=>testCalculationRequestRetainsLocalPresentation(2,true)).then(()=>testCalculationRequestRetainsLocalPresentation(2)).then(()=>testCalculationRequestRetainsLocalPresentation(1)).then(()=>console.log('Saved chart studio: stable styles, complete ranges, formatting, theme, accessible legend, transient toggles, alternative data, calculation-only requests and v1 compatibility passed.')).catch(error=>{console.error(error);process.exitCode=1;});
