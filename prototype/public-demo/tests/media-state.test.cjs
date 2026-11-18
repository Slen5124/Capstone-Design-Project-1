const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const handlers={};
class Image { constructor(){this.alt='사진';this.hidden=false;this.src='bad';this.classes=new Set();this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x)};}getAttribute(){return this.src;}after(el){this.notice=el;} }
const document={addEventListener:(n,fn)=>handlers[n]=fn,createElement:()=>({setAttribute(){},append(...items){this.items=items;},remove(){this.removed=true;}})};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../src/media-state.js'),'utf8'),{document,HTMLImageElement:Image});
const img=new Image();handlers.error({target:img});assert(img.classes.has('media-failed'));assert.match(img.notice.items[0].textContent,/불러오지 못/);
const notice=img.notice;notice.items[1].onclick();assert(notice.removed);handlers.error({target:img});assert(img.classes.has('media-failed'));const retryNotice=img.notice;handlers.load({target:img});assert(retryNotice.removed);assert.equal(img.classes.has('media-failed'),false);
console.log('PASS: failed photo shows retry and clears when loaded');
