/* Turn-based rehearsal: persisted sessions, traceable delivery changes, consent-aware audio and outcomes. */
const { state, api, e, icon, human, date, badge, heading, empty, traits, display, toast, busy, invalid, download } = await import('./core.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content || 'dev'));

const { startLive, stopLive, interruptLive, isLive } = await import('./live.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content||'dev'));

const SIGNALS=[
  ['frustration','Frustration','I am frustrated. Nobody has resolved my complaint.'],
  ['confusion','Confusion',"I am confused. Can you explain that in simple steps?"],
  ['interest','Interest','That sounds interesting. Tell me more about the next step.'],
  ['rush','In a rush','I am busy and in a hurry. Please keep this short.'],
  ['language_switch','Language switch','कृपया हिंदी में बात करें।'],
];
let recorder=null;
let stream=null;
let recordTimer=null;
let recordChunks=[];
let audioUrl='';
let discardRecording=false;
let restoredSession=false;

/** An opaque resume pointer is safe to retain; transcripts remain on the authorized server. */
function saveSessionId(id){try{if(id)sessionStorage.setItem('persona-last-session',id);else sessionStorage.removeItem('persona-last-session');}catch{}}

/** Lab startup keeps the public text path usable without microphone or paid providers. */
export async function renderLab() {
  if(!state.voiceConfig)state.voiceConfig=await api('/voice/status');
  // Store only the opaque session ID; the server checks its owner cookie on every restoration.
  if(!restoredSession){restoredSession=true;let id;try{id=sessionStorage.getItem('persona-last-session');}catch{}if(id){try{state.session=await api('/sessions/'+encodeURIComponent(id));state.result=state.session.outcome || null;state.labSellerId=state.session.seller_id;}catch{saveSessionId('');toast('Previous rehearsal is unavailable in this browser session.');}}}
  if(!state.labSellers) state.labSellers=(await api('/sellers?page_size=24')).items || [];
  const sellers=[...state.labSellers];
  const requestedId=state.labSellerId || state.session?.seller_id;
  // Private choices are appended only after operator authorization; the demo cache remains public-only.
  if(state.operator && requestedId && !sellers.some(item=>item.id===requestedId)){const selectedSeller=state.seller?.id===requestedId?state.seller:await api('/sellers/'+encodeURIComponent(requestedId));sellers.push(selectedSeller);}
  const session=state.session;
  const selected=state.labSellerId || session?.seller_id || sellers[0]?.id;
  const stopped=session?.status==='finished' || session?.status==='stopped' || session?.status==='ended' || state.labStopped;
  return heading('CONVERSATION LAB','Listen. Understand. Adapt.','Rehearse a real state change. Every delivery switch has a reason and an inspectable before / after.',session?`<button class="button" data-action="new-session">New rehearsal</button>`:'')+`<div class="demo-note">${icon('shield')}<span>VANI introduces itself as an AI assistant. Choose text, recorded turns or <strong>live duplex voice</strong>, with explicit method labels. No outbound call is placed.</span></div>${!session?`<section class="panel"><div class="panel-heading"><div><h2>Choose who you're speaking with</h2><p>Start with one seller, then compare a contrasting persona.</p></div>${icon('users')}</div><div class="panel-body"><form id="start-session" class="stack"><div class="field"><label for="lab-seller">Seller for rehearsal</label><select id="lab-seller" name="seller_id">${sellers.map(seller=>`<option value="${e(seller.id)}" ${selected===seller.id?'selected':''}>${e(seller.name)} · ${e(human(seller.persona_tag))} · ${e(seller.language || 'Language unknown')}</option>`).join('')}</select></div><label class="check-row"><input type="checkbox" name="use_llm" ${state.operator?'':'disabled'}><span>LLM-assisted rehearsal <span class="muted">${state.operator?'· live provider usage':'· verified session required'}</span></span></label><div><button class="button button-primary" type="submit" ${sellers.length?'':'disabled'}>${icon('play')} Start rehearsal</button></div></form></div></section><div class="grid-three page-section">${SIGNALS.slice(0,3).map(([id,label,text],i)=>`<article class="panel"><div class="panel-body"><div class="eyebrow">SIGNAL 0${i+1}</div><h2 style="font-size:19px">${label}</h2><p class="small muted" style="margin-top:12px">“${e(text)}”</p><p class="small divider">${['Acknowledge the issue. Lower pressure. Focus on resolution.','Slow down. Use one concrete step at a time.','Offer useful detail. Ask for a clear next step.'][i]}</p></div></article>`).join('')}</div><div class="index-note"><strong>Five required signals, one transparent record.</strong>Frustration, confusion, interest, rush and language switch are all available as sample turns once the rehearsal starts.</div>`:`<div class="lab-layout"><div><section class="panel"><div class="panel-heading"><div><h2>${e(sellers.find(s=>s.id===session.seller_id)?.name || state.seller?.name || 'Seller rehearsal')}</h2><p>${e(String(session.id).slice(0,12))} · ${e(human(session.method || (session.use_llm?'LLM assisted':'Rule rehearsal')))}</p></div>${badge(session.status || 'active')}</div><div id="conversation" class="conversation" aria-label="Conversation transcript" aria-live="polite">${renderTurns(session)}</div>${!stopped?`<form id="send-turn" class="composer"><div class="field"><label for="turn-text">Your seller response</label><textarea id="turn-text" name="text" rows="3" placeholder="Type what the seller says…" maxlength="2000"></textarea></div><div class="sample-signals" aria-label="Example signals">${SIGNALS.map(([signal,label])=>`<button type="button" class="signal-chip ${hasSignal(signal)?'detected':''}" data-action="sample-signal" data-signal="${signal}">${hasSignal(signal)?'✓ ':''}${label}</button>`).join('')}</div><div class="row"><button class="button button-primary" type="submit">Send turn ${icon('arrow')}</button><button class="button" type="button" data-action="finish-session">Finish &amp; evaluate</button></div><div id="turn-feedback" role="status"></div></form>`:`<div class="composer"><p class="small muted">${state.labStopped?'The seller requested no further contact. Turn submission has stopped.':'This rehearsal has ended. Start a new session to continue testing.'}</p>${!state.result?'<button class="button button-primary" data-action="finish-session" style="margin-top:12px">View call outcome</button>':''}</div>`}</section>${renderOutcome(state.result)}<section class="panel page-section"><div class="panel-heading"><div><h2>Voice bench</h2><p>Sarvam transcription and synthesis · operator-only</p></div>${badge(state.operator?'Session verified':'Sign in required','neutral')}</div><div class="panel-body">${voiceBenchControls()}<label class="voice-consent"><input type="checkbox" id="voice-consent" ${state.operator?'':'disabled'}><span>I consent to sending microphone audio to Sarvam. Live recognized speech is submitted automatically as rehearsal turns. Avoid private seller information.</span></label><div class="row" style="margin-top:18px"><button class="button" data-action="record-voice" id="record-button" ${state.operator && !stopped && !!navigator.mediaDevices?.getUserMedia && !!window.MediaRecorder?'':'disabled'}>${icon('mic')} Record a turn</button><button class="button" data-action="synthesize-voice" ${state.operator?'':'disabled'}>${icon('play')} Speak latest reply</button></div><p class="small muted" style="margin-top:12px">${state.operator?'Record up to 25 seconds. Review the transcript before sending it. Playback uses the selected language and pace.':'Sign in through Access & password to enable provider calls. Text rehearsal is available now.'}</p><div id="voice-feedback" role="status"></div><div id="voice-player"></div><div class="live-voice divider"><h3>Live duplex rehearsal</h3><p class="small muted">Speak while VANI talks. Speech-start interrupts playback. Realtime transcripts, delivery switches and response timings stay visible. Maximum 3 minutes; no outbound call.</p><div class="row" style="margin-top:16px"><button class="button button-primary" id="live-start" data-action="live-start" ${state.operator && !stopped && !!navigator.mediaDevices?.getUserMedia && !!window.AudioWorkletNode?'':'disabled'}>Start live voice</button><button class="button" id="live-interrupt" data-action="live-interrupt" disabled>Interrupt</button><button class="button" id="live-stop" data-action="live-stop" disabled>Stop live voice</button></div><p id="live-status" role="status" class="small" style="margin-top:16px">Microphone off. Consent required.</p><p id="live-partial" class="live-partial" aria-live="polite"></p><p id="live-metrics" class="small muted"></p></div></div></section></div><aside class="stack" aria-label="Delivery and adaptation"><section class="panel"><div class="panel-heading"><div><h2>Current delivery</h2><p>Persona state after the latest turn</p></div>${icon('wave')}</div><div class="panel-body">${state.latestSignal?`<div class="row" style="margin-bottom:16px">${badge(signalName(state.latestSignal),'accent')}<span class="small muted">Latest detected signal</span></div>`:''}${traits(session.persona,true)}${state.latestDecision?.applied===false?`<p class="demo-note">Delivery held: ${e(state.latestDecision.reason||state.latestDecision.status||'cooldown policy')}. Detected signals remain separate from actual switches.</p>`:''}${state.latestLatency!=null?`<div class="stat-line divider">Response ${e(state.latestLatency)} ms · ${e(state.latestMethod || 'method not supplied')}</div>`:''}</div></section><section class="panel"><div class="panel-heading"><div><h2>Switch log <span class="muted">(${(session.switches || []).length})</span></h2><p>Delivery changes, with reasons</p></div><button class="icon-button" data-action="export-switches" aria-label="Download switch log">${icon('download')}</button></div><div class="panel-body">${renderSwitches(session.switches || [])}</div></section></aside></div>`}`;
}

/** Accepts both role messages and seller/reply turn records without rendering undefined text. */
function renderTurns(session) {
  const turns=session.turns || [];
  const opening=session.opening || session.persona?.plan?.opening;
  const messages=[];
  if(opening && (!turns.length || turns[0]?.role==='user' || turns[0]?.text!==opening)) messages.push({role:'assistant',text:opening,method:session.method || session.persona?.method});
  for(const turn of turns){
    if(turn.role) messages.push({...turn,text:turn.text || turn.content});
    else {if(turn.text || turn.seller_text || turn.user) messages.push({role:'user',text:turn.text || turn.seller_text || turn.user}); if(turn.reply || turn.assistant) messages.push({role:'assistant',text:turn.reply || turn.assistant,method:turn.method});}
  }
  if(!messages.length) return empty('The conversation is ready','Send a seller response to test how delivery adapts.');
  return messages.map(message=>`<div class="message ${message.role==='user' || message.role==='seller'?'message-user':''}"><div class="message-meta"><span>${message.role==='user' || message.role==='seller'?'Seller':'VANI · AI assistant'}</span>${message.method?`<span>${e(message.method)}</span>`:''}</div><div class="message-content" ${/[\u0900-\u097F]/.test(message.text||'')?'lang="hi"':''}>${e(message.text)}</div></div>`).join('');
}
/** Signal payloads can carry detector details; the display keeps the signal label readable. */
function signalName(value) { return typeof value==='string'?value:value?.name || value?.signal || value?.type || 'neutral'; }
/** Marks examples that already caused a real recorded switch in this session. */
function hasSignal(signal) { return (state.session?.switches || []).some(item=>signalName(item.signal)===signal) || signalName(state.latestSignal)===signal; }
/** Immutable before/after records are available on demand without overwhelming the transcript. */
function renderSwitches(items) { return items.length ? items.map((item,i)=>`<article class="switch-item"><div class="spread"><h3>${e(human(signalName(item.signal || item.trigger || item.reason_type)))}</h3><span class="mono">#${i+1}</span></div><p>${e(item.reason || item.description || 'Delivery updated from the detected signal.')}</p><span class="mono">${date(item.created_at || item.timestamp || item.at)}</span><details class="native-details"><summary>Before → after</summary><pre class="code-block">${e(JSON.stringify({before:item.before || item.previous,after:item.after || item.current},null,2))}</pre></details></article>`).join('') : '<p class="small muted">No delivery changes yet. Try one of the five sample signals and inspect the resulting change here.</p>'; }
/** Outcome scores are explicitly a rubric, never represented as a conversion probability. */
function renderOutcome(result) { if(!result)return '';const conversion=result.conversion || {};return `<section class="outcome" aria-label="Call outcome"><div class="eyebrow">REHEARSAL OUTCOME</div>${badge(result.recommendation || 'Review required','success')}<h2>A clear next move.</h2><p>${e(result.summary)}</p><ul>${(result.next_actions || []).map(item=>`<li>${e(display(item))}</li>`).join('')}</ul>${conversion.score!=null?`<div class="score-row"><strong class="score-value">${e(conversion.score)}<span style="font-size:17px">/${e(conversion.max_score)}</span></strong><div><h3>Sales readiness rubric</h3><p class="small">An observed-signal score, not a conversion probability.</p></div></div>`:''}${(conversion.criteria || []).length?`<details class="native-details"><summary>See scored criteria</summary><ul>${conversion.criteria.map(item=>`<li>${e(typeof item==='string'?item:item.label || item.criterion || item.name || display(item))}${typeof item==='object'?': '+e(item.reason || (item.score ?? item.met ?? '')):''}</li>`).join('')}</ul></details>`:''}<p class="small">${e(result.switch_count ?? state.session?.switches?.length ?? 0)} delivery changes retained in the switch log.</p></section>`; }
/** Language follows an explicit persona field and stays adjustable on the voice bench. */
function voiceLanguage(){const value=state.session?.persona?.fields?.language?.value || 'hi-IN';return ({Hindi:'hi-IN',English:'en-IN',Tamil:'ta-IN',Telugu:'te-IN',Bengali:'bn-IN',Marathi:'mr-IN',hi:'hi-IN',en:'en-IN'})[value] || (String(value).includes('-')?value:'hi-IN');}
/** Provider-supported pace is clamped without inventing unsupported pitch controls. */
function voicePace(){const value=Number(state.session?.persona?.fields?.pace?.value);return Number.isFinite(value)&&value>=.5&&value<=2?value:1;}
/** Repaints only the lab and scrolls the conversation, preserving session state in memory. */
async function refreshLab(){if(state.route!=='lab')return;document.querySelector('#main').innerHTML=await renderLab();const transcript=document.querySelector('#conversation');if(transcript)transcript.scrollTop=transcript.scrollHeight;}

/** Handles session creation and turns; the backend remains responsible for authorization and ownership. */
export async function handleLabSubmit(form,values,button){
  if(form.id==='start-session'){
    const sellerId=String(values.get('seller_id')||'');if(!sellerId){invalid(form.querySelector('select'),'Choose a demo seller to begin.');return true;}
    state.labSellerId=sellerId;state.session=await busy(button,'Starting rehearsal…',()=>api('/sessions',{method:'POST',body:JSON.stringify({seller_id:sellerId,use_llm:values.get('use_llm')==='on'})}));saveSessionId(state.session.id);state.result=null;state.labStopped=false;state.latestSignal=null;state.latestLatency=null;await refreshLab();toast('Rehearsal started. Try a seller response.');return true;
  }
  if(form.id==='send-turn'){
    if(isLive())throw new Error('Stop live voice before sending a typed turn.');
    const text=String(values.get('text')||'').trim();if(!text){invalid(form.querySelector('textarea'),'Enter a seller response or choose a sample signal.');return true;}
    const response=await busy(button,'Adapting the reply…',()=>api('/sessions/'+encodeURIComponent(state.session.id)+'/turns',{method:'POST',body:JSON.stringify({text})}));
    state.session={...state.session,...response,id:state.session.id,turns:response.turns || [...(state.session.turns||[]),{text,reply:response.reply}],switches:response.switches || state.session.switches,persona:response.persona || state.session.persona};state.latestSignal=response.signal;state.latestLatency=response.latency_ms;state.latestMethod=response.method;state.latestDecision=response.decision;state.labStopped=signalName(response.signal)==='stop' || response.status==='stopped';await refreshLab();return true;
  }
  return false;
}

/** Actions return false when unrelated to the lab, allowing the main controller to continue. */
export async function handleLabAction(action,button){
  switch(action){
    case 'live-start':await startLive(acceptLiveTurn);return true;
    case 'live-stop':stopLive();return true;
    case 'live-interrupt':interruptLive();return true;
    case 'sample-signal':{const text=button.dataset.signal==='language_switch' && voiceLanguage()==='hi-IN'?'Please speak in English.':SIGNALS.find(item=>item[0]===button.dataset.signal)?.[2];const input=document.querySelector('#turn-text');input.value=text || '';input.focus();return true;}
    case 'new-session':stopRecording();saveSessionId('');state.session=null;state.result=null;state.labStopped=false;state.latestSignal=null;await refreshLab();return true;
    case 'finish-session':state.result=await busy(button,'Evaluating the outcome…',()=>api('/sessions/'+encodeURIComponent(state.session.id)+'/finish',{method:'POST'}));state.session.status='finished';stopRecording();await refreshLab();document.querySelector('.outcome')?.scrollIntoView({behavior:'smooth',block:'center'});toast('Outcome saved with its readiness rubric.');return true;
    case 'export-switches':download('switch-log-'+state.session.id+'.json',{session_id:state.session.id,seller_id:state.session.seller_id,switches:state.session.switches || [],outcome:state.result});toast('Switch log downloaded.');return true;
    case 'record-voice':await recordVoice(button);return true;
    case 'synthesize-voice':await synthesize(button);return true;
    default:return false;
  }
}

/** Stops recording on navigation or session end; never leaves microphone tracks open. */
export function stopRecording(){stopLive();document.querySelector('#voice-player audio')?.pause();if(audioUrl){URL.revokeObjectURL(audioUrl);audioUrl='';}discardRecording=true;clearTimeout(recordTimer);if(recorder && recorder.state!=='inactive')recorder.stop();stream?.getTracks().forEach(track=>track.stop());stream=null;}
/** Explicit microphone consent precedes upload; ASR text is reviewed before submission as a turn. */
async function recordVoice(button){
  if(recorder?.state==='recording'){clearTimeout(recordTimer);recorder.stop();stream?.getTracks().forEach(track=>track.stop());return;}
  const consent=document.querySelector('#voice-consent');if(!consent?.checked){invalid(consent,'Consent to speech processing before recording.');return;}
  if(!state.operator)throw new Error('Sign in through Access & password first.');
  const original=button.innerHTML;
  button.disabled=true;button.innerHTML='<span class="spinner"></span> Requesting microphone…';
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(state.route!=='lab' || !button.isConnected){stream.getTracks().forEach(track=>track.stop());stream=null;return;}
    const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
    recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
  }catch(error){stream?.getTracks().forEach(track=>track.stop());stream=null;throw error;}
  finally{if(button.isConnected){button.disabled=false;button.innerHTML=original;}}
  recordChunks=[];discardRecording=false;
  recorder.addEventListener('dataavailable',event=>{if(event.data.size)recordChunks.push(event.data);});
  recorder.addEventListener('stop',async()=>{
    clearTimeout(recordTimer);stream?.getTracks().forEach(track=>track.stop());stream=null;
    if(discardRecording || state.route!=='lab')return;
    const blob=new Blob(recordChunks,{type:recorder.mimeType});const body=new FormData();body.append('file',blob,recorder.mimeType.includes('mp4')?'rehearsal.m4a':'rehearsal.webm');body.append('language',document.querySelector('#voice-language')?.value || voiceLanguage());
    const feedback=document.querySelector('#voice-feedback');
    try{const result=await busy(button,'Transcribing your turn…',()=>api('/voice/transcribe',{method:'POST',body}));const input=document.querySelector('#turn-text');if(input){input.value=result.text || '';input.focus();}if(feedback)feedback.innerHTML=`<p class="small divider">${e(result.provider || 'Speech provider')} · ${e(result.language || '')}. Review the transcript before sending. ${result.diarization?'Diarization: '+e(display(result.diarization)):''}</p>`;toast('Transcript ready for review.');}catch(error){if(feedback)feedback.innerHTML=`<p class="inline-error" role="alert">${e(error.message)}</p>`;toast(error.message,true);}finally{if(button.isConnected){button.innerHTML=icon('mic')+' Record a turn';button.disabled=false;}}
  });
  recorder.start();button.innerHTML='<span class="live-dot"></span> Stop recording';document.querySelector('#voice-feedback').innerHTML='<p class="small divider">Recording… press Stop when finished. Maximum 25 seconds.</p>';recordTimer=setTimeout(()=>{if(recorder?.state==='recording'){recorder.stop();stream?.getTracks().forEach(track=>track.stop());}},25000);
}
/** Synthesis uses the latest actual assistant reply and exposes native accessible playback controls. */
async function synthesize(button){
  if(!state.operator)throw new Error('Sign in through Access & password first.');
  const turns=state.session.turns || [];const last=[...turns].reverse().find(turn=>turn.reply || turn.role==='assistant' || turn.assistant);
  const text=last?.reply || last?.text || last?.content || last?.assistant || state.session.persona?.plan?.opening;
  if(!text)throw new Error('Generate a reply first, then play it back.');
  const language=document.querySelector('#voice-language').value,profile=selectedProfile();const pace=Number(document.querySelector('#speech-pace').value);if(pace<profile.pace_min||pace>profile.pace_max){invalid(document.querySelector('#speech-pace'),'Choose a pace between '+profile.pace_min+' and '+profile.pace_max+'.');return;}
  const options={text,language,pace,speaker:document.querySelector('#voice-speaker')?.value,profile:profile.id};
  if(profile.pitch_supported)options.pitch=Number(document.querySelector('#speech-pitch').value);
  const result=await busy(button,'Synthesizing the reply…',()=>api('/voice/synthesize',{method:'POST',body:JSON.stringify(options)}));
  const binary=atob(result.audio_base64);const bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(new Blob([bytes],{type:result.content_type || 'audio/wav'}));
  const player=document.querySelector('#voice-player');player.innerHTML=`<audio class="audio-player" controls src="${audioUrl}" aria-label="Synthesized assistant reply"></audio><p class="small muted">${e(result.provider || 'Sarvam')} · ${e(language)} · ${e(pace)}× pace · ${e(result.model||profile.model)}${profile.pitch_supported?' · pitch '+e(options.pitch):' · provider-default pitch'}</p>`;toast('Speech ready. Press play to listen.');
}

/** Update transcript/delivery without tearing down the active microphone widget. */
function acceptLiveTurn(response){
  state.session={...state.session,...response,id:state.session.id};state.latestSignal=response.signal;state.latestLatency=response.latency_ms;state.latestMethod=response.method;state.latestDecision=response.decision;
  const transcript=document.querySelector('#conversation');if(transcript){transcript.innerHTML=renderTurns(state.session);transcript.scrollTop=transcript.scrollHeight;}
  const aside=document.querySelector('.lab-layout aside');if(aside){aside.innerHTML='<section class="panel"><div class="panel-body"><h2>Current delivery</h2>'+traits(state.session.persona,true)+'<h2 class="divider">Switch log</h2>'+renderSwitches(response.switches||[])+'</div></section>';}
}

function voiceSpeaker(){return state.voiceConfig?.persona_speakers?.[state.session?.seller_id]||state.session?.persona?.fields?.speaker?.value||state.voiceConfig?.default_speaker;}
function languageChoices(selected){const names={'hi-IN':'Hindi','en-IN':'English','ta-IN':'Tamil','te-IN':'Telugu','bn-IN':'Bengali','mr-IN':'Marathi','gu-IN':'Gujarati','kn-IN':'Kannada','ml-IN':'Malayalam','pa-IN':'Punjabi','od-IN':'Odia'};return (state.voiceConfig?.languages||[]).map(code=>`<option value="${e(code)}" ${code===selected?'selected':''}>${e(names[code]||code)} · ${e(code)}</option>`).join('');}

/** Voice controls follow the server's model capability registry, not guessed shared bounds. */
function voiceProfiles(){return state.voiceConfig?.profiles?.length?state.voiceConfig.profiles:[{id:'v3',model:'bulbul:v3',enabled:true,selected:true,speakers:state.voiceConfig?.speakers||[],default_speaker:state.voiceConfig?.default_speaker,pace_min:.5,pace_max:2,pitch_supported:false}];}
function profileAvailable(p){return p.enabled&&!['deprecated','beta_access_required'].includes(p.availability);}
function selectedProfile(){const profiles=voiceProfiles();return profiles.find(p=>p.id===state.voiceProfile&&profileAvailable(p))||profiles.find(p=>p.selected&&profileAvailable(p))||profiles.find(profileAvailable)||profiles[0];}
function voiceBenchControls(){const profile=selectedProfile(),speaker=profile.speakers.includes(voiceSpeaker())?voiceSpeaker():profile.default_speaker;return `<div id="voice-bench-controls"><div class="field"><label for="voice-profile">Speech profile</label><select id="voice-profile">${voiceProfiles().map(p=>`<option value="${e(p.id)}" ${p.id===profile.id?'selected':''} ${profileAvailable(p)?'':'disabled'}>${e(p.model)} · ${p.pitch_supported?'pitch profile':'natural voice'}${profileAvailable(p)?'':' · '+e(human(p.availability||'disabled'))}</option>`).join('')}</select><small>Profile controls available voices, pace and pitch. Accent matching is not an independent control.</small>${voiceProfiles().filter(p=>!profileAvailable(p)).map(p=>`<p class="small muted">${e(p.model)}: ${e(p.capability_reason||human(p.availability||'disabled in configuration'))}</p>`).join('')}</div><div class="grid-two" style="margin-top:16px"><div class="field"><label for="voice-language">Speech language</label><select id="voice-language">${languageChoices(voiceLanguage())}</select></div><div class="field"><label for="speech-pace">Speech pace</label><input id="speech-pace" type="number" min="${e(profile.pace_min)}" max="${e(profile.pace_max)}" step="0.1" value="${e(voicePace())}"></div></div><div class="grid-two" style="margin-top:16px"><div class="field"><label for="voice-speaker">Voice choice</label><select id="voice-speaker">${profile.speakers.map(name=>`<option value="${e(name)}" ${name===speaker?'selected':''}>${e(human(name))}</option>`).join('')}</select></div><div class="field"><label for="speech-pitch">Pitch</label><input id="speech-pitch" type="number" min="${e(profile.pitch_min??0)}" max="${e(profile.pitch_max??0)}" step="0.05" value="0" ${profile.pitch_supported?'':'disabled'}><small>${profile.pitch_supported?'Supported by this model; verify the audible effect.':'Provider default. This profile does not support pitch control.'}</small></div></div><p class="small muted" style="margin-top:12px">Formality and warmth affect reply wording. A documented capability is separate from verified account access.</p></div>`;}
export function handleLabChange(event){if(event.target.id!=='voice-profile')return;if(isLive())stopLive();state.voiceProfile=event.target.value;const node=document.querySelector('#voice-bench-controls');if(node)node.outerHTML=voiceBenchControls();}
