'use strict';
const lightbox=document.querySelector('.lightbox');
if(lightbox){
  let opener;
  document.querySelectorAll('[data-gallery]').forEach(button=>button.addEventListener('click',()=>{
    opener=button;
    const image=button.querySelector('img');
    lightbox.querySelector('img').src=image.src;
    lightbox.querySelector('img').alt=image.alt;
    lightbox.querySelector('p').textContent=button.textContent.trim();
    lightbox.showModal();
  }));
  lightbox.querySelector('button').addEventListener('click',()=>lightbox.close());
  lightbox.addEventListener('click',e=>{if(e.target===lightbox){const r=lightbox.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)lightbox.close();}});
  lightbox.addEventListener('close',()=>opener?.focus());
}
const modelButton=document.querySelector('#load-model');
if(modelButton)modelButton.addEventListener('click',async()=>{
  modelButton.disabled=true;
  const status=document.querySelector('#model-status');
  status.textContent='正在加载三维模型…';
  try{const viewer=await import('/model-viewer.js?v=20261115');await viewer.startModel();}
  catch(error){status.textContent='三维加载未成功，可查看上方预览或下载 GLB 后打开。';modelButton.disabled=false;console.error(error);}
});
