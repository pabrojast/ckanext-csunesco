/* Behavior checks against the actual shared core and production portal painter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const core = require('../assets/js/cs-chart-presentation.js');
const def = {version:2,title:'Water',mode:'numeric',chart:'line',axes:{},presentation:{palette:'accessible',series:{},legend:{display:'show',position:'right',title:'Sites'},numberFormat:{decimals:2,useGrouping:false},axisTitles:{x:'Date',y:'cm'},referenceBands:[]}};
const keyA='s:0000000000000001', keyB='s:0000000000000002';
const payload={mode:'numeric',series:[{key:keyA,name:'A',xy:[{x:'2026-01-01',y:2},{x:'2026-02-01',y:1000}]},{key:keyB,name:'B',xy:[{x:'2026-01-01',y:3}]}],labels:[],y_max:10};
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
const categorical={mode:'category',labels:['One','Two'],category_keys:['c:0000000000000001','c:0000000000000002'],series:[{name:'Water',points:[4,8]}]};
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
function Chart(_canvas,config){this.config=config;this.data=config.data;this.hidden={};this.update=()=>{};this.setDatasetVisibility=(i,value)=>{this.hidden[i]=!value;};this.toggleDataVisibility=(i)=>{this.hidden[i]=!this.hidden[i];};charts.push(this);}
const sandbox={Chart,CSChartPresentation:core,document:{readyState:'complete',documentElement:{lang:'en'},querySelectorAll:()=>[],createElement:tag=>new Node(tag)},console};sandbox.window=sandbox;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/js/cs-charts.js'),'utf8'),sandbox);
function paint(d,p){const figure=new Node('figure'),container=new Node('div');figure.appendChild(container);container.setAttribute('data-definition',JSON.stringify(d));container.setAttribute('data-type',d.chart);assert.equal(sandbox.csunescoCharts.paint(container,p),true);return {figure,chart:charts.at(-1)};}
const numeric=paint(limited,payload);const buttons=walk(numeric.figure).filter(n=>n.tagName==='button');
assert.equal(buttons.length,2);assert.equal(buttons[0].attrs['aria-pressed'],'true');buttons[0].events.click();assert.equal(buttons[0].attrs['aria-pressed'],'false');assert.equal(numeric.chart.hidden[0],true);buttons[0].events.click();assert.equal(numeric.chart.hidden[0],false);
assert.ok(walk(numeric.figure).some(n=>n.className==='cs-chart-range-warning'));
const details=walk(numeric.figure).find(n=>n.tagName==='details');details.open=true;details.events.toggle();assert.ok(walk(details).some(n=>n.tagName==='td'&&n.textContent==='1000.00'),'alternative table retains clipped observations');
category.chart='bar';category.axes={};const bars=paint(category,categorical);const catButtons=walk(bars.figure).filter(n=>n.tagName==='button');catButtons[1].events.click();assert.equal(bars.chart.data.datasets[0].data[1],null);catButtons[1].events.click();assert.equal(bars.chart.data.datasets[0].data[1],8);assert.equal(categorical.series[0].points[1],8,'legend toggles are transient');
const legacy=structuredClone(def);legacy.version=1;const old=paint(legacy,payload);assert.equal(old.chart.config.options.scales.y.max,10,'v1 rendering is unchanged');
console.log('Saved chart studio: stable styles, complete ranges, formatting, theme, accessible legend, transient toggles, alternative data and v1 compatibility passed.');
