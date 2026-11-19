const assert=require('node:assert/strict');
const explorer=require('../assets/js/cs-chart-explorer.js');
const core=require('../assets/js/cs-chart-presentation.js');
const a='s:0000000000000001',b='s:0000000000000002';
const definition={version:2,title:'Published',caption:'Unchanged',mode:'numeric',chart:'line',field:'temperature',bucket:'raw',axes:{yMin:0},filters:{sites:['A'],facets:{weather:['dry']}},source_id:'public-source',presentation:{palette:'accessible',series:{[a]:{color:'#123456',label:'Upstream',lineStyle:'dotted',pointStyle:'triangle'}},legend:{display:'hide',position:'right',title:'Sites'},numberFormat:{decimals:'auto',useGrouping:true},axisTitles:{x:'Date',y:'Value'},referenceBands:[{id:'r',min:2,max:4,color:'#0066AA',label:'Reference'}]}};
const payload={definition_version:2,mode:'numeric',labels:[],category_keys:[],series:[{key:a,name:'A',xy:[{x:'2026-02-01T10:15:00.001234Z',y:3}]},{key:b,name:'B',xy:[{x:'2026-02-03',y:7}]}]};
const before=JSON.stringify({definition,payload});
explorer.assertPayload(definition,payload);
let state=explorer.createState(definition);state.type='bar';state.palette='mono';state.hidden[b]=true;state.ranges.xMin='2026-02-01';state.ranges.xMax='2026-02-01';
let view=explorer.deriveView(definition,payload,state);
assert.equal(view.definition.chart,'bar');assert.equal(view.payload.series.length,1);assert.equal(view.definition.axes.xMax,Date.parse('2026-02-01T23:59:59.999Z'));
assert.equal(view.definition.axes.xMin,Date.parse('2026-02-01T00:00:00Z'));
assert.equal(view.definition.presentation.series[a].color,undefined);assert.equal(view.definition.presentation.series[a].label,'Upstream');assert.equal(view.definition.presentation.series[a].pointStyle,'triangle');assert.equal(view.definition.presentation.series[a].lineStyle,'dotted');assert.deepEqual(view.definition.presentation.referenceBands,definition.presentation.referenceBands);assert.deepEqual(view.definition.filters,definition.filters);assert.equal(view.definition.source_id,'public-source');
assert.equal(JSON.stringify({definition,payload}),before,'temporary changes never mutate published data/configuration');
const publishedB=core.resolveStyle(definition,b,'B');const recoloredB=core.resolveStyle(view.definition,b,'B');assert.equal(recoloredB.lineStyle,publishedB.lineStyle);assert.equal(recoloredB.pointStyle,publishedB.pointStyle);
state.palette='published';assert.equal(explorer.deriveView(definition,payload,state).definition.presentation.series[a].color,'#123456');
for(const bad of ['Infinity','NaN','0x10','word']){state=explorer.createState(definition);state.ranges.yMax=bad;assert.throws(()=>explorer.deriveView(definition,payload,state));}
for(const bad of ['2026-02-30','0000-01-01','10000-01-01']){state=explorer.createState(definition);state.ranges.xMin=bad;assert.throws(()=>explorer.deriveView(definition,payload,state));}
state=explorer.createState(definition);state.ranges.yMax='0';assert.throws(()=>explorer.deriveView(definition,payload,state));
state=explorer.createState(definition);state.ranges.xMin='2026-03-01';state.ranges.xMax='2026-02-01';assert.throws(()=>explorer.deriveView(definition,payload,state));
state=explorer.createState(definition);state.hidden={[a]:true,[b]:true};assert.equal(explorer.deriveView(definition,payload,state).payload.series.length,0);
assert.deepEqual(explorer.deriveView(definition,payload,explorer.createState(definition)),{definition,payload},'restore returns exact original values');
const category=structuredClone(definition);category.mode='category';category.chart='bar';category.axes={};const cats={definition_version:2,mode:'category',labels:['Dry','Wet'],category_keys:['c:0000000000000001','c:0000000000000002'],series:[{key:a,name:'Weather',points:[5,9]}]};explorer.assertPayload(category,cats);
state=explorer.createState(category);state.type='pie';state.hidden[cats.category_keys[0]]=true;state.ranges.yMax='invalid while disabled';view=explorer.deriveView(category,cats,state);assert.deepEqual(view.payload.labels,['Wet']);assert.deepEqual(view.payload.category_keys,[cats.category_keys[1]]);assert.deepEqual(view.payload.series[0].points,[9]);assert.deepEqual(view.definition.axes,{});assert.equal(core.legendEntries(view.definition,view.payload)[0].key,cats.category_keys[1]);
for(const bad of [{...payload,definition_version:1},{...payload,definition_version:undefined},{...payload,series:[{...payload.series[0],key:'label'}]},{...payload,series:[payload.series[0],payload.series[0]]},{...cats,category_keys:['c:0000000000000001']},{...payload,category_keys:['c:0000000000000001']}])assert.throws(()=>explorer.assertPayload(bad.mode==='category'?category:definition,bad));
assert.equal(core.formatDate(payload.series[0].xy[0].x,definition,'en'),'2026-02-01 10:15:00.001234 UTC');
console.log('Visitor explorer: ranges, UTC end-of-day, types, palettes, category identity, reset, immutability, raw precision and response gates passed.');
