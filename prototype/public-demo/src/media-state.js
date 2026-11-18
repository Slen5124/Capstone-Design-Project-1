(() => {
  const failed=new WeakMap();
  document.addEventListener('error',event=>{
    const img=event.target;
    if(!(img instanceof HTMLImageElement)||!img.getAttribute('src')||img.hidden||failed.has(img))return;
    const box=document.createElement('div');box.className='media-error';box.setAttribute('role','status');
    const label=document.createElement('span');label.textContent=(img.alt?img.alt+' · ':'')+'사진을 불러오지 못했습니다.';
    const retry=document.createElement('button');retry.type='button';retry.textContent='다시 불러오기';
    retry.onclick=()=>{retry.disabled=true;retry.textContent='불러오는 중…';const src=img.getAttribute('src');failed.delete(img);box.remove();img.classList.remove('media-failed');img.src=src;};
    box.append(label,retry);img.classList.add('media-failed');img.after(box);failed.set(img,box);
  },true);
  document.addEventListener('load',event=>{
    const img=event.target;if(!(img instanceof HTMLImageElement))return;
    failed.get(img)?.remove();failed.delete(img);img.classList.remove('media-failed');
  },true);
})();
