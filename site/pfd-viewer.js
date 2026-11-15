'use strict';
const stage=document.querySelector('#pfd-stage'),status=document.querySelector('#pfd-status'),detail=document.querySelector('#pfd-detail');
async function start(){
  const [svgText,zones]=await Promise.all([
    fetch('/showcase/fr2023/pfd.svg').then(r=>{if(!r.ok)throw Error(r.status);return r.text();}),
    fetch('/showcase/fr2023/zones.json').then(r=>{if(!r.ok)throw Error(r.status);return r.json();})
  ]);
  const parsed=new DOMParser().parseFromString(svgText,'image/svg+xml');
  if(parsed.querySelector('parsererror'))throw Error('SVG parse failed');
  const svg=document.importNode(parsed.documentElement,true);
  stage.prepend(svg);
  const full=[0,0,17000,5500];let view=[...full],drag=null;
  const draw=()=>svg.setAttribute('viewBox',view.join(' '));
  function hide(){detail.hidden=true;}
  function show(text){detail.querySelector('p').textContent=text;detail.hidden=false;}
  function fit(){view=[...full];draw();hide();svg.querySelectorAll('.found').forEach(el=>el.classList.remove('found'));status.textContent='全图 · 拖动平移，滚轮或按钮缩放；可搜索位号及物流编号。';}
  function zoom(f,cx=.5,cy=.5){const nw=view[2]*f,nh=view[3]*f;if(nw<100||nw>full[2]*3)return;view=[view[0]+(view[2]-nw)*cx,view[1]+(view[3]-nh)*cy,nw,nh];draw();}
  stage.addEventListener('wheel',e=>{e.preventDefault();const r=svg.getBoundingClientRect();zoom(e.deltaY>0?1.18:1/1.18,(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height);},{passive:false});
  stage.addEventListener('pointerdown',e=>{if(e.target.closest('.pfd-detail'))return;drag={x:e.clientX,y:e.clientY,v:[...view],moved:false};stage.setPointerCapture(e.pointerId);});
  stage.addEventListener('pointermove',e=>{if(!drag)return;const r=svg.getBoundingClientRect(),scale=Math.max(drag.v[2]/r.width,drag.v[3]/r.height);if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>3)drag.moved=true;view=[drag.v[0]-(e.clientX-drag.x)*scale,drag.v[1]-(e.clientY-drag.y)*scale,drag.v[2],drag.v[3]];draw();});
  stage.addEventListener('pointerup',e=>{if(!drag)return;const moved=drag.moved;drag=null;if(moved)return;const el=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-search]');if(el)show(el.querySelector('title')?.textContent||'');else if(!e.target.closest('.pfd-detail'))hide();});
  stage.addEventListener('pointercancel',()=>drag=null);
  document.querySelector('#pfd-fit').addEventListener('click',fit);
  document.querySelector('#pfd-plus').addEventListener('click',()=>zoom(1/1.5));
  document.querySelector('#pfd-minus').addEventListener('click',()=>zoom(1.5));
  detail.querySelector('button').addEventListener('click',hide);
  document.querySelector('#pfd-zone').addEventListener('change',e=>{if(e.target.value==='all')return fit();const [l,t,r,b]=zones[e.target.value];view=[l-60,t-70,r-l+120,b-t+140];draw();hide();status.textContent=e.target.selectedOptions[0].textContent;});
  document.querySelector('#pfd-search').addEventListener('submit',e=>{
    e.preventDefault();const q=document.querySelector('#pfd-query').value.trim().toLowerCase();if(!q)return;
    const items=[...svg.querySelectorAll('[data-search]')];
    let el=items.find(n=>n.getAttribute('data-search').toLowerCase().split('|').includes(q));
    if(!el)el=items.find(n=>n.getAttribute('data-search').toLowerCase().includes(q));
    if(!el){status.textContent='没有匹配的位号或物流编号，请尝试 S001 或 TK-CRUDE。';show('没有匹配结果');return;}
    items.forEach(n=>n.classList.remove('found'));el.classList.add('found');
    const b=el.getBBox();view=[b.x-240,b.y-150,Math.max(b.width+480,800),Math.max(b.height+300,500)];draw();
    show(el.querySelector('title')?.textContent||'');status.textContent='已定位：'+document.querySelector('#pfd-query').value;
  });
  stage.addEventListener('keydown',e=>{if(e.target.closest('.pfd-detail'))return;const step=view[2]*.1;const actions={ArrowLeft:()=>view[0]-=step,ArrowRight:()=>view[0]+=step,ArrowUp:()=>view[1]-=step,ArrowDown:()=>view[1]+=step,'+':()=>zoom(1/1.5),'=':()=>zoom(1/1.5),'-':()=>zoom(1.5),Home:fit,Escape:hide};if(actions[e.key]){e.preventDefault();actions[e.key]();draw();}});
  fit();stage.dataset.loaded='true';
}
start().catch(error=>{status.textContent='在线流程图加载未成功，可返回案例页下载 PDF 查看。';console.error(error);});
