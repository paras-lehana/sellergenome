/* Genuine duplex voice. Provider keys never enter this module or socket URLs. */
const { BASE, state, api, invalid, toast } = await import('./core.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content||'dev'));
let socket=null, stream=null, context=null, source=null, node=null, timer=null;
let generation=0, serverGeneration=0, audioBlocked=false, nextStart=0, players=new Set(), decode=Promise.resolve(), active=false, starting=false, token=0;
export function isLive(){return active||starting;}
function status(text){const target=document.querySelector('#live-status');if(target)target.textContent=text;}
function barge(){generation++;for(const player of players){try{player.stop();}catch{}}players.clear();nextStart=0;}
function controls(){const on=isLive();for(const id of ['voice-profile','voice-speaker','voice-language','speech-pace'])document.getElementById(id)?.toggleAttribute('disabled',on);const pitch=document.getElementById('speech-pitch');const profile=(state.voiceConfig?.profiles||[]).find(p=>p.id===document.getElementById('voice-profile')?.value);pitch?.toggleAttribute('disabled',on||!profile?.pitch_supported);document.querySelector('#live-start')?.toggleAttribute('disabled',on||!state.operator);document.querySelector('#live-stop')?.toggleAttribute('disabled',!on);document.querySelector('#live-interrupt')?.toggleAttribute('disabled',!on);document.querySelector('#record-button')?.toggleAttribute('disabled',on||!state.operator);document.querySelector('[data-action=synthesize-voice]')?.toggleAttribute('disabled',on||!state.operator);document.querySelector('#turn-text')?.toggleAttribute('disabled',on);document.querySelector('#send-turn button[type="submit"]')?.toggleAttribute('disabled',on);}
/** Navigation, key clearing and finish close tracks, audio, upstream and local timers. */
export function stopLive(){
  token++;starting=false;active=false;clearTimeout(timer);barge();
  if(socket){if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'end'}));socket.close();socket=null;}
  node?.disconnect();source?.disconnect();node=null;source=null;stream?.getTracks().forEach(track=>track.stop());stream=null;
  context?.close().catch(()=>{});context=null;controls();status('Live voice stopped. Microphone released.');
}
async function audio(message){
  if(audioBlocked || message.generation!==serverGeneration)return;
  const ownContext=context,epoch=generation,chunkGeneration=message.generation;
  const bytes=Uint8Array.from(atob(message.audio),c=>c.charCodeAt(0));
  // Serialize decode to preserve chunk order on both engines; stale decodes cannot play.
  decode=decode.catch(()=>{}).then(async()=>{
    if(!active||context!==ownContext||epoch!==generation)return;
    const buffer=await ownContext.decodeAudioData(bytes.buffer);
    if(!active||context!==ownContext||epoch!==generation)return;
    const player=ownContext.createBufferSource();player.buffer=buffer;player.connect(ownContext.destination);
    const when=Math.max(ownContext.currentTime+.03,nextStart);nextStart=when+buffer.duration;
    players.add(player);player.onended=()=>players.delete(player);player.start(when);
    document.querySelector('#live-metrics')?.setAttribute('data-audio-generation',String(chunkGeneration));
  }).catch(()=>{status('A streaming audio chunk could not play. Stop and retry; text remains visible.');});
}
export async function startLive(onTurn){
  if(isLive())return;
  const consent=document.querySelector('#voice-consent');if(!consent?.checked){invalid(consent,'Consent to live provider processing and automatic rehearsal turns first.');return;}
  if(!state.operator||!state.session)throw new Error('Start an authorized rehearsal first.');
  starting=true;serverGeneration=0;audioBlocked=false;const ownToken=++token;controls();status('Requesting microphone and secure live connection…');
  try{
    context=new (window.AudioContext||window.webkitAudioContext)();await context.resume();status('Microphone access requested…');
    const captured=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(ownToken!==token){captured.getTracks().forEach(track=>track.stop());return;}stream=captured;
    await context.audioWorklet.addModule(BASE+'/static/pcm-worklet.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content||'dev'));
    if(ownToken!==token)return;
    const grant=await api('/sessions/'+encodeURIComponent(state.session.id)+'/live-ticket',{method:'POST',body:JSON.stringify({consent:true,speaker:document.querySelector('#voice-speaker')?.value,profile:document.querySelector('#voice-profile')?.value||'v3',...((state.voiceConfig?.profiles||[]).find(p=>p.id===document.querySelector('#voice-profile')?.value)?.pitch_supported?{pitch:Number(document.querySelector('#speech-pitch').value)}:{})})});
    if(ownToken!==token)return;
    source=context.createMediaStreamSource(stream);node=new AudioWorkletNode(context,'persona-pcm');const silent=context.createGain();silent.gain.value=0;source.connect(node);node.connect(silent);silent.connect(context.destination);
    const url=new URL(BASE+'/api/sessions/'+encodeURIComponent(state.session.id)+'/live',location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
    const ws=new WebSocket(url);socket=ws;
    ws.onopen=()=>{if(ownToken===token)ws.send(JSON.stringify({ticket:grant.ticket}));else ws.close();};
    node.port.onmessage=event=>{if(active&&ws.readyState===WebSocket.OPEN&&ws.bufferedAmount<64000)ws.send(event.data);};
    ws.onmessage=event=>{
      if(ownToken!==token)return;
      let message;try{message=JSON.parse(event.data);}catch{return;}
      switch(message.event){
        case 'ready':serverGeneration=message.generation??serverGeneration;audioBlocked=false;clearTimeout(timer);active=true;starting=false;controls();status('Live · listening and speaking · '+message.model+' · '+message.slot);timer=setTimeout(stopLive,grant.max_seconds*1000);break;
        case 'transcript.partial':{const el=document.querySelector('#live-partial');if(el)el.textContent=message.text;break;}
        case 'transcript.final':{const el=document.querySelector('#live-partial');if(el)el.textContent='Recognized: '+message.text;break;}
        case 'interruption':serverGeneration=message.generation;audioBlocked=false;barge();status('Interrupted · listening for your next turn');break;
        case 'turn':onTurn(message.result);if(message.result.status!=='active')stopLive();else status('Live · adapting to '+message.result.signal);break;
        case 'audio':audio(message);break;
        case 'latency':{const el=document.querySelector('#live-metrics');if(el){const stages=message.stage_timings||{};el.textContent='Response to first audio: '+message.response_audio_ms+' ms'+(stages.recognition_ms!=null?' · Recognition '+stages.recognition_ms+' ms':'')+(stages.model_ms!=null?' · Model '+stages.model_ms+' ms':'')+' · Server timings; hardware playback not measured.';}break;}
        case 'fallback':status(message.message);break;
        case 'error':status(message.message);toast(message.message,true);break;
      }
    };
    ws.onerror=()=>{if(ownToken===token)status('Live connection failed. Check workspace access and provider status.');};
    ws.onclose=()=>{if(ownToken===token)stopLive();};
    timer=setTimeout(()=>{if(starting){stopLive();toast('Live connection did not become ready.',true);}},20000);
  }catch(error){if(ownToken===token){stopLive();status('Microphone off · '+error.message);}throw error;}
}
export function interruptLive(){audioBlocked=true;barge();if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({event:'interrupt'}));status('Playback interrupted. Keep speaking.');}
