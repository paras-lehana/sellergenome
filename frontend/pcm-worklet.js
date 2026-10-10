/* Mono PCM16, 16 kHz, 200 ms frames. Resample the actual device rate, never assume it. */
class PersonaPCM extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Int16Array(3200);this.used=0;this.phase=0;this.previous=0;this.first=true;}
  process(inputs,outputs){
    for(const output of outputs)for(const channel of output)channel.fill(0);
    const input=inputs[0]?.[0];if(!input)return true;
    const step=sampleRate/16000;
    for(let i=0;i<input.length;i++){
      const current=input[i];if(this.first){this.previous=current;this.first=false;}
      while(this.phase<1){
        const value=this.previous+(current-this.previous)*this.phase;
        this.samples[this.used++]=Math.round(Math.max(-1,Math.min(1,value))*32767);
        if(this.used===3200){const frame=new ArrayBuffer(6400),view=new DataView(frame);for(let n=0;n<3200;n++)view.setInt16(n*2,this.samples[n],true);this.port.postMessage(frame,[frame]);this.used=0;}
        this.phase+=step;
      }
      this.phase-=1;this.previous=current;
    }
    return true;
  }
}
registerProcessor('persona-pcm',PersonaPCM);
