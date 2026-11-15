import * as THREE from '/showcase/vendor/three.module.js';
import {OrbitControls} from '/showcase/vendor/OrbitControls.js';
import {GLTFLoader} from '/showcase/vendor/GLTFLoader.js';

export async function startModel(){
  const frame=document.querySelector('#model-frame');
  const status=document.querySelector('#model-status');
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setSize(frame.clientWidth,frame.clientHeight);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.05;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.domElement.setAttribute('aria-label','天正杯苯乙烯分厂三维模型，拖动旋转、滚轮缩放，点击查看对象名称');
  renderer.domElement.setAttribute('tabindex','0');
  const scene=new THREE.Scene();scene.background=new THREE.Color('#e9f1f8');
  scene.add(new THREE.HemisphereLight(0xffffff,0x718496,1.4));
  const sun=new THREE.DirectionalLight(0xffffff,2.6);scene.add(sun);scene.add(sun.target);
  const fill=new THREE.DirectionalLight(0xd7eaff,.6);fill.position.set(-80,80,-100);scene.add(fill);
  const camera=new THREE.PerspectiveCamera(40,frame.clientWidth/frame.clientHeight,0.1,10000);
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.enableDamping=false;controls.maxPolarAngle=Math.PI*.49;controls.screenSpacePanning=true;
  const loaded=await new GLTFLoader().loadAsync('/showcase/tianzheng/scene.glb');
  const model=loaded.scene;scene.add(model);
  const box=new THREE.Box3().setFromObject(model),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
  const radius=Math.max(size.x,size.y,size.z);
  sun.position.copy(center).add(new THREE.Vector3(radius*.7,radius,radius*.5));sun.target.position.copy(center);
  sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
  Object.assign(sun.shadow.camera,{left:-radius*.75,right:radius*.75,top:radius*.75,bottom:-radius*.75,near:radius*.01,far:radius*4});
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.normalBias=radius*.001;sun.shadow.bias=-.001;
  model.traverse(mesh=>{if(mesh.isMesh){mesh.castShadow=true;mesh.receiveShadow=true;}});
  controls.minDistance=radius*.03;controls.maxDistance=radius*5;
  camera.near=radius/10000;camera.far=radius*20;camera.updateProjectionMatrix();
  const render=()=>renderer.render(scene,camera);
  function view(mode){
    controls.target.copy(center);
    const direction=(mode==='top'?new THREE.Vector3(0,1,.001):new THREE.Vector3(1,1,1.15)).normalize();
    const right=new THREE.Vector3().crossVectors(camera.up,direction).normalize(),up=new THREE.Vector3().crossVectors(direction,right);
    const tangent=Math.tan(THREE.MathUtils.degToRad(camera.fov/2));let distance=0;
    for(const x of [-.5,.5])for(const y of [-.5,.5])for(const z of [-.5,.5]){
      const corner=new THREE.Vector3(size.x*x,size.y*y,size.z*z),depth=corner.dot(direction);
      distance=Math.max(distance,depth+Math.abs(corner.dot(right))/(tangent*camera.aspect),depth+Math.abs(corner.dot(up))/tangent);
    }
    distance*=1.12;
    camera.position.copy(center).addScaledVector(direction,distance);
    camera.lookAt(center);controls.update();render();
  }
  frame.querySelector('img').remove();frame.querySelector('.model-start').remove();
  frame.prepend(renderer.domElement);
  const label=document.createElement('div');label.className='model-label';label.hidden=true;label.setAttribute('aria-live','polite');frame.append(label);
  let selected=[];
  function clear(){for(const [mesh,material] of selected) {mesh.material.dispose();mesh.material=material;}selected=[];}
  const raycaster=new THREE.Raycaster();let down;
  renderer.domElement.addEventListener('pointerdown',e=>{down=[e.clientX,e.clientY]});
  renderer.domElement.addEventListener('pointerup',e=>{
    if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;
    const rect=renderer.domElement.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);
    const hit=raycaster.intersectObject(model,true)[0];clear();label.hidden=!hit;
    if(hit){
      let object=hit.object;
      while(object.parent&&object.parent!==model)object=object.parent;
      label.textContent=object.name||hit.object.name||'场景对象';
      object.traverse(mesh=>{if(mesh.isMesh&&mesh.material&&!Array.isArray(mesh.material)&&mesh.material.emissive){const original=mesh.material;mesh.material=original.clone();mesh.material.emissive.set('#155aa3');mesh.material.emissiveIntensity=.45;selected.push([mesh,original]);}});
    }
    render();
  });
  controls.addEventListener('change',render);
  document.querySelector('#model-reset').addEventListener('click',()=>{clear();label.hidden=true;view('iso')});
  document.querySelector('#model-top').addEventListener('click',()=>view('top'));
  renderer.domElement.addEventListener('keydown',e=>{
    if(e.key==='Home'){e.preventDefault();view('iso');}
    if(e.key==='+'||e.key==='='){camera.position.lerp(controls.target,.12);controls.update();}
    if(e.key==='-'){camera.position.sub(controls.target).multiplyScalar(1.12).add(controls.target);controls.update();}
  });
  new ResizeObserver(()=>{camera.aspect=frame.clientWidth/frame.clientHeight;camera.updateProjectionMatrix();renderer.setSize(frame.clientWidth,frame.clientHeight);render();}).observe(frame);
  document.querySelectorAll('[data-model-control]').forEach(button=>button.disabled=false);
  status.textContent='模型已加载 · 拖动旋转 / 双指缩放 / 点击对象查看名称；键盘 +、− 缩放，Home 复位。';
  frame.dataset.loaded='true';view('iso');
}
