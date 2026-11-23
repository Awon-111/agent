/* Local Vue workspace. Model configuration is read-only and owned by the backend. */
const {createApp, ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick} = Vue;
const iconPaths = {
  cross:'M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z', grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  folder:'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M3 9h18',
  chevron:'m9 5 7 7-7 7', arrow:'M4 12h16 m-6-6 6 6-6 6', back:'M20 12H4 m6-6-6 6 6 6', check:'m5 12 4 4L19 6', plus:'M12 4v16 M4 12h16',
  shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z m-4 9 3 3 5-6', spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  report:'M14 3H5v18h14V8z M14 3v5h5 M8 12h8 M8 16h6', lock:'M7 10V7a5 5 0 0 1 10 0v3 M5 10h14v11H5z M12 14v3',
  info:'M12 8v.1 M12 11v6 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', refresh:'M20 7a9 9 0 1 0 1 9 M20 3v5h-5',
  search:'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0', image:'M3 3h18v18H3z m0 14 6-6 4 4 3-3 5 5 M16 7h.1',
  upload:'M12 16V3 m-5 5 5-5 5 5 M4 15v6h16v-6', clock:'M12 7v6l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  doctor:'M6 3v6a5 5 0 0 0 10 0V3 M4 3h4 M14 3h4 M11 14v3a4 4 0 0 0 8 0v-3 M21 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  spine:'M11 2c-5 5 6 8 1 13s-2 6-2 7 M7 4l7 2 M8 9l8-1 M10 13l7 2 M6 18l8 2',
  joint:'M8 3v7l4 4-1 7 M16 3v7l-4 4 4 7 M5 7h6 M14 7h5 M7 10l4 1 M14 11l4-1',
  bone:'M7 4a3 3 0 1 0-4 4l6 7a3 3 0 1 0 4 4l2-2 2-2a3 3 0 1 0 4-4l-6-7a3 3 0 1 0-4-4z M8 16l8-8 M10 18l8-8',
  settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  send:'m3 3 19 9-19 9 4-9-4-9z M7 12h15', tool:'m14 7 3 3 4-4a6 6 0 0 1-8 8L5 22l-3-3 8-8a6 6 0 0 1 8-8z',
  chat:'M21 15a3 3 0 0 1-3 3H8l-5 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3z M7 8h10 M7 12h6',
  external:'M14 3h7v7 M21 3 11 13 M10 3H3v18h18v-7', key:'M14 11a5 5 0 1 1 2 2l-9 9H3v-4z', close:'m6 6 12 12 M6 18 18 6'
};
const Icon = {props:['name'], template:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="path"/></svg>',computed:{path(){return iconPaths[this.name] || iconPaths.cross;}}};
const scenarios = [{id:'spine',name:'脊柱侧弯',subtitle:'矫形规划',icon:'spine'},{id:'joint',name:'关节置换',subtitle:'假体匹配',icon:'joint'},{id:'trauma',name:'骨折创伤',subtitle:'内固定推荐',icon:'bone'}];
const samples = {
  spine:{cobb_angle:48,flexibility_pct:55,coronal_balance_mm:28,apex_region:'thoracic'},
  joint:{joint_type:'hip',bone_diameter_mm:52,canal_width_mm:13,offset_mm:42,bone_quality:'normal'},
  trauma:{fracture_site:'tibia',fracture_pattern:'comminuted',displacement_mm:18,articular_involvement:true,soft_tissue:'compromised'}
};
const fieldSets = {
  spine:[{key:'cobb_angle',label:'Cobb 角',unit:'°',min:10,max:100},{key:'flexibility_pct',label:'柔韧性',unit:'%',min:0,max:100},{key:'coronal_balance_mm',label:'冠状面失衡',unit:'mm',min:0,max:100},{key:'apex_region',label:'顶椎区域',type:'select',options:[['thoracic','胸椎'],['thoracolumbar','胸腰段'],['lumbar','腰椎']]}],
  joint:[{key:'joint_type',label:'置换关节',type:'select',options:[['hip','髋关节'],['knee','膝关节']]},{key:'bone_quality',label:'骨质情况',type:'select',options:[['normal','一般'],['low','偏低']]},{key:'bone_diameter_mm',label:'骨性尺寸',unit:'mm',min:35,max:90},{key:'canal_width_mm',label:'髓腔宽度',unit:'mm',min:5,max:25},{key:'offset_mm',label:'偏心距',unit:'mm',min:0,max:70}],
  trauma:[{key:'fracture_site',label:'骨折部位',type:'select',options:[['femur','股骨'],['tibia','胫骨'],['radius','桡骨']]},{key:'fracture_pattern',label:'骨折形态',type:'select',options:[['simple','简单'],['comminuted','粉碎']]},{key:'displacement_mm',label:'移位距离',unit:'mm',min:0,max:50},{key:'soft_tissue',label:'软组织情况',type:'select',options:[['intact','完整'],['compromised','受损']]},{key:'articular_involvement',label:'关节面受累',type:'checkbox'}]
};

createApp({components:{Icon},setup(){
  const workspacePane=ref('chat');
  const view=ref('new'),scenario=ref('spine'),cases=ref([]),detail=ref(null),selectedPlanId=ref('');
  const busy=ref(false),configBusy=ref(false),configLoading=ref(true),search=ref(''),caseFilter=ref('all');
  const form=reactive({code:'',age:32,sex:'female',clinical_summary:''}),measurements=ref({...samples.spine});
  const evidenceStatus=ref({count:0,scenarios:{}}),evidenceItems=ref([]),evidenceQuery=ref(''),evidenceScenario=ref('spine'),evidenceBusy=ref(false);
  const review=reactive({reviewer:'',note:'',status:'approved'}),chatDraft=ref(''),conversationEl=ref(null);
  const config=ref({provider:'deepseek',model:'deepseek-flash',configured:false,key_source:'none',mode:'rules_fallback',fallback_available:true});
  const connectionState=ref('unverified'),connectionMessage=ref(''),configError=ref('');
  const toastMessage=ref(''),toastError=ref(false),pollError=ref(''),runLaunching=ref(false);
  let pollTimer=null,pollGeneration=0,toastTimer=null;
  const scenarioById=id=>scenarios.find(s=>s.id===id)||scenarios[0];
  const scenarioInfo=computed(()=>scenarioById(scenario.value));
  const activeFields=computed(()=>fieldSets[scenario.value].filter(f=>!(f.key==='offset_mm'&&measurements.value.joint_type==='knee')));
  const plan=computed(()=>detail.value?.plans.find(p=>p.id===selectedPlanId.value)||detail.value?.plans[0]);
  const result=computed(()=>plan.value?.result||{}),runs=computed(()=>detail.value?.agent_runs||[]);
  const latestRun=computed(()=>runs.value[runs.value.length-1]);
  const isLocalReport=computed(()=>result.value.agent?.source==='local_rules'||result.value.agent?.provider==='local_rules');
  const localConversation=computed(()=>!config.value.configured||isLocalRun(latestRun.value));
  const runBusy=computed(()=>runLaunching.value||runs.value.some(r=>r.status==='running'));
  const filteredCases=computed(()=>cases.value.filter(c=>(caseFilter.value==='all'||c.scenario===caseFilter.value)&&c.code.toLowerCase().includes(search.value.toLowerCase())));
  const approvedCount=computed(()=>cases.value.filter(c=>c.latest_plan_status==='approved').length);
  const connectionLabel=computed(()=>configLoading.value?'读取服务状态中':configError.value?'状态读取失败':!config.value.configured?'LLM 未连接':connectionState.value==='connected'?'已连接 DeepSeek':connectionState.value==='failed'?'连接失败':connectionState.value==='testing'?'正在测试连接':'已配置 · 待验证');
  const configSourceLabel=computed(()=>({dotenv:'后端 .env',environment:'进程环境变量',none:'未配置'})[config.value.key_source]||'后端配置');
  const sceneGuide=computed(()=>({spine:{method:'按 Cobb 角和柔韧性估算示意目标，比较两条规划路径。',outputs:['目标 Cobb 角与规则依据','标准矫形、分期评估候选','柔韧性单因素分析'],checks:['确认角度与柔韧性的测量口径','结合站立位与侧屈位影像复核']},joint:{method:'对示例器械目录计算加权尺寸差，逐项展示偏差与评分。',outputs:['髋或膝假体候选排序','外径、髓腔等尺寸偏差','评分明细与尺寸敏感性'],checks:['输入尺寸应经过影像标定','膝关节不使用偏心距参数']},trauma:{method:'根据骨折部位、形态、关节面和软组织条件，为固定候选加减分。',outputs:['钢板、髓内钉、外固定架排序','条件加减分的具体明细','移位变化与候选评分'],checks:['核对骨折分型和软组织状态','补充关节面及血运神经评估']}})[scenario.value]);
  const measurementRows=computed(()=>{if(!detail.value)return[];const m=detail.value.case.measurements;return fieldSets[detail.value.case.scenario].filter(f=>!(f.key==='offset_mm'&&m.joint_type==='knee')).map(f=>({label:f.label,value:f.type==='select'?f.options.find(o=>o[0]===m[f.key])?.[1]||m[f.key]:f.type==='checkbox'?m[f.key]?'是':'否':`${m[f.key]} ${f.unit||''}`}));});
  const selectedVersion=computed(()=>detail.value?detail.value.plans.length-detail.value.plans.findIndex(p=>p.id===plan.value?.id):0);
  watch(selectedPlanId,()=>{review.reviewer='';review.note='';review.status='approved';});
  function toast(message,error=false){toastMessage.value=message;toastError.value=error;clearTimeout(toastTimer);toastTimer=setTimeout(()=>toastMessage.value='',6000);}
  async function api(url,options={}){let response;try{response=await fetch(url,{...options,cache:'no-store'});}catch(_){throw new Error('无法连接本地服务，请确认 Python 服务正在运行。');}let data;try{data=await response.json();}catch(_){throw new Error('服务返回了无法读取的响应，请刷新后重试。');}if(!response.ok)throw new Error(data.error||'操作失败，请重试。');return data;}
  const post=(url,payload)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload||{})});
  function stopPolling(){clearTimeout(pollTimer);pollTimer=null;pollGeneration++;}
  function go(name){stopPolling();pollError.value='';view.value=name;window.scrollTo({top:0,behavior:'smooth'});}
  function changeScenario(id){scenario.value=id;measurements.value={...samples[id]};}
  function fillSample(){measurements.value={...samples[scenario.value]};form.code='DEMO-'+({spine:'SP',joint:'JT',trauma:'TR'})[scenario.value]+'-'+Date.now().toString().slice(-5);form.age=32;form.sex='female';toast('已填入当前场景示例，可直接编辑测量参数。');}
  async function loadCases(){const data=await api('/api/cases');cases.value=data.cases;}
  async function loadConfig(){configLoading.value=true;configError.value='';try{config.value=await api('/api/agent/config');connectionState.value=['connected','failed'].includes(config.value.connection_status)?config.value.connection_status:'unverified';}catch(e){configError.value=e.message;throw e;}finally{configLoading.value=false;}}
  async function showCases(){try{await loadCases();go('cases');}catch(e){toast(e.message,true);}}
  async function showSettings(){go('settings');connectionMessage.value='';try{await loadConfig();}catch(e){toast(e.message,true);}}
  async function searchEvidence(){evidenceBusy.value=true;try{const data=await api('/api/evidence/search?scenario='+encodeURIComponent(evidenceScenario.value)+'&q='+encodeURIComponent(evidenceQuery.value));evidenceItems.value=data.items;}catch(e){toast(e.message,true);}finally{evidenceBusy.value=false;}}
  async function showEvidence(){go('evidence');try{evidenceStatus.value=await api('/api/evidence/status');await searchEvidence();}catch(e){toast(e.message,true);}}
  async function refreshDetail(id,keepPlan=true,expectedGeneration=null){const data=await api('/api/cases/'+id);if(expectedGeneration!==null&&expectedGeneration!==pollGeneration)return null;data.agent_runs=data.agent_runs||[];const sameCase=detail.value?.case.id===id;detail.value=data;if(!keepPlan||!sameCase||!data.plans.some(p=>p.id===selectedPlanId.value))selectedPlanId.value=data.plans[0]?.id||'';if(!sameCase||!keepPlan){review.reviewer='';review.note='';chatDraft.value='';}return data;}
  async function showDetail(id,keepPlan=false){workspacePane.value='chat';stopPolling();await refreshDetail(id,keepPlan);view.value='detail';pollError.value='';window.scrollTo({top:0,behavior:'smooth'});const running=runs.value.find(r=>r.status==='running');if(running)startPolling(running.run_id||running.id,id);await scrollConversation();}
  async function safeShowDetail(id){try{await showDetail(id);}catch(e){toast(e.message,true);}}
  function mergeRun(run){if(!detail.value||detail.value.case.id!==run.case_id)return;const id=run.run_id||run.id;const i=detail.value.agent_runs.findIndex(r=>(r.run_id||r.id)===id);if(i<0)detail.value.agent_runs.push(run);else detail.value.agent_runs[i]={...detail.value.agent_runs[i],...run};}
  async function scrollConversation(){await nextTick();if(conversationEl.value)conversationEl.value.scrollTop=conversationEl.value.scrollHeight;}
  function startPolling(runId,caseId){stopPolling();pollError.value='';let generation=pollGeneration;async function poll(){try{const run=await api('/api/agent/runs/'+runId);if(generation!==pollGeneration||view.value!=='detail'||detail.value?.case.id!==caseId)return;const element=conversationEl.value,nearBottom=!element||element.scrollHeight-element.scrollTop-element.clientHeight<70;mergeRun(run);if(run.status==='running'){if(nearBottom)await scrollConversation();pollTimer=setTimeout(poll,1200);return;}stopPolling();generation=pollGeneration;const terminalGeneration=pollGeneration;const data=await refreshDetail(caseId,true,terminalGeneration);if(!data||terminalGeneration!==pollGeneration||view.value!=='detail')return;if(run.plan_id)selectedPlanId.value=run.plan_id;await loadCases();if(terminalGeneration!==pollGeneration||view.value!=='detail')return;if(run.status==='completed'){if(run.provider==='deepseek'&&run.mode==='llm')connectionState.value='connected';toast(isLocalRun(run)?run.plan_id?'本地演示方案已保存，未调用 LLM。':'已返回本地规则说明，未调用 LLM。':run.plan_id?'智能体已完成分析，报告草稿已保存。':'智能体已回复。');}else toast(run.error||'智能体运行失败，请查看执行记录后重试。',true);await scrollConversation();}catch(e){if(generation!==pollGeneration)return;pollError.value=e.message+' 后台任务可能仍在继续，可点击“恢复查看”。';}}poll();}
  function resumePolling(){const run=runs.value.find(r=>r.status==='running')||latestRun.value;if(run&&detail.value)startPolling(run.run_id||run.id,detail.value.case.id);}
  async function launchRun(caseId,message,mode){runLaunching.value=true;try{const payload=message?{message}:{};if(mode)payload.mode=mode;const data=await post('/api/cases/'+caseId+(message?'/chat':'/analyze'),payload);await refreshDetail(caseId,true);if(!runs.value.some(r=>(r.run_id||r.id)===data.run_id))mergeRun({run_id:data.run_id,case_id:caseId,status:'running',provider:data.provider,model:data.model,mode:data.mode,question:message||'请分析本病例，调用专科工具并生成规划报告。',events:[]});startPolling(data.run_id,caseId);await scrollConversation();}finally{runLaunching.value=false;}}
  async function createCase(){if(busy.value)return;busy.value=true;let caseId='';try{const m={...measurements.value};if(scenario.value==='joint'&&m.joint_type==='knee')delete m.offset_mm;const created=await post('/api/cases',{...form,scenario:scenario.value,measurements:m});caseId=created.id;await showDetail(caseId);await loadCases();await launchRun(caseId);}catch(e){if(caseId)await refreshDetail(caseId,true).catch(()=>{});toast(e.message,true);}finally{busy.value=false;}}
  async function reanalyze(){if(!detail.value||busy.value||runBusy.value)return;busy.value=true;try{await launchRun(detail.value.case.id);}catch(e){toast(e.message,true);}finally{busy.value=false;}}
  function failureDescription(run){const message=run?.error||'';return /JSON|格式|完整性校验|结构化|病例引用|病例相似/.test(message)?'模型回复未通过报告完整性校验，尚未保存方案。病例和输入参数已保留，可以直接重试生成报告。':message||'本次任务未完成，请稍后重试。';}
  async function retryRun(run){if(!detail.value||busy.value||runBusy.value)return;busy.value=true;try{await launchRun(detail.value.case.id,run?.kind==='chat'?run.question:undefined,isLocalRun(run)?'local_rules':undefined);}catch(e){toast(e.message,true);}finally{busy.value=false;}}
  async function sendChat(message){const text=typeof message==='string'?message.trim():chatDraft.value.trim();if(!text||!detail.value||runBusy.value||busy.value)return;busy.value=true;try{await launchRun(detail.value.case.id,text,localConversation.value?'local_rules':undefined);chatDraft.value='';}catch(e){toast(e.message,true);}finally{busy.value=false;}}
  async function useLocalRules(run){if(!detail.value||busy.value||runBusy.value)return;busy.value=true;try{await launchRun(detail.value.case.id,run?.kind==='chat'?run.question:undefined,'local_rules');}catch(e){toast(e.message,true);}finally{busy.value=false;}}
  function isLocalRun(run){return !!run&&(run.provider==='local_rules'||run.mode==='rules_fallback');}
  async function testConnection(){if(configBusy.value)return;configBusy.value=true;try{await loadConfig();if(!config.value.configured)throw new Error('后端尚未配置模型。请在项目 .env 中填写 DEEPSEEK_API_KEY 并重启服务。');connectionState.value='testing';connectionMessage.value='正在验证后端到 DeepSeek 的连接…';const data=await post('/api/agent/test');if(!data.connected)throw new Error(data.message||'未能验证连接。');await loadConfig();connectionState.value='connected';connectionMessage.value=data.message||'DeepSeek 已返回有效响应，可以启动智能体。';toast('后端模型连接测试通过。');}catch(e){connectionState.value='failed';connectionMessage.value=e.message;toast(e.message,true);}finally{configBusy.value=false;}}
  async function submitReview(){if(!plan.value)return;busy.value=true;try{await post('/api/plans/'+plan.value.id+'/review',{...review});await refreshDetail(detail.value.case.id,true);await loadCases();toast('复核结论已归档。');}catch(e){toast(e.message,true);}finally{busy.value=false;}}
  async function uploadImage(event){const file=event.target.files[0];if(!file)return;busy.value=true;try{if(!['image/png','image/jpeg'].includes(file.type))throw new Error('请选择 PNG 或 JPEG 图片。');if(file.size>5000000)throw new Error('图片不能超过 5 MB。');await api('/api/cases/'+detail.value.case.id+'/image',{method:'POST',headers:{'Content-Type':file.type},body:file});await refreshDetail(detail.value.case.id,true);toast('影像附件已保存，仅供人工查看。');}catch(e){toast(e.message,true);}finally{busy.value=false;event.target.value='';}}
  function sensitivityHeight(value){return Math.max(4,value/Math.max(...(result.value.sensitivity?.points||[]).map(p=>p.y),1)*100);}
  const formatTime=v=>v?new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
  const statusLabel=s=>({pending:'待复核',approved:'已通过',rejected:'已退回'})[s]||'待生成',sexLabel=s=>({female:'女',male:'男',other:'其他 / 未说明'})[s];
  const runLabel=s=>({running:'执行中',completed:'已完成',failed:'执行失败'})[s]||s,eventIcon=t=>({model:'spark',local:'tool',tool:'tool',report:'report',repair:'refresh',error:'info'})[t]||'clock';
  const auditLabel=s=>({case_created:'创建病例',plan_generated:'生成方案',plan_reviewed:'完成复核',image_uploaded:'上传影像',agent_started:'启动智能体',agent_completed:'智能体完成',agent_answered:'智能体完成追问',agent_failed:'智能体失败'})[s]||s;
  onMounted(async()=>{const outcomes=await Promise.allSettled([loadCases(),loadConfig()]);outcomes.forEach(r=>{if(r.status==='rejected')toast(r.reason.message,true);});});
  onBeforeUnmount(()=>{stopPolling();clearTimeout(toastTimer);});
  return {retryRun,failureDescription,workspacePane,evidenceStatus,evidenceItems,evidenceQuery,evidenceScenario,evidenceBusy,searchEvidence,showEvidence,view,scenario,scenarios,scenarioInfo,scenarioById,activeFields,form,measurements,cases,detail,selectedPlanId,plan,result,runs,latestRun,isLocalReport,localConversation,isLocalRun,runBusy,runLaunching,busy,review,search,caseFilter,filteredCases,approvedCount,config,configBusy,configLoading,configError,connectionState,connectionMessage,connectionLabel,configSourceLabel,sceneGuide,chatDraft,conversationEl,pollError,measurementRows,selectedVersion,toastMessage,toastError,go,changeScenario,fillSample,createCase,showCases,showSettings,safeShowDetail,reanalyze,sendChat,useLocalRules,testConnection,submitReview,uploadImage,resumePolling,sensitivityHeight,formatTime,statusLabel,sexLabel,runLabel,eventIcon,auditLabel};
}}).mount('#app');
