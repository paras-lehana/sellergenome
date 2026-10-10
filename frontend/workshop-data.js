/* This manifest is a bundled synthetic teaching resource, never an API response cache. */
const version=encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content||'dev');
let manifest;
export async function workshopData(){
 if(!manifest){const response=await fetch(new URL('./workshop-manifest.json?v='+version,import.meta.url));if(!response.ok)throw new Error('The bundled workshop could not load. Reload this release.');manifest=await response.json();}
 return manifest;
}
export function workshopTarget(id){return manifest?.targets.find(target=>target.id===id);}
export function workshopScenario(id){for(const section of manifest?.sections||[]){const scenario=section.scenarios.find(item=>item.id===id);if(scenario)return{section,scenario};}return null;}
