'use strict';
const menuButton=document.querySelector('.menu-button');
const menu=document.querySelector('#mobile-menu');
function closeMenu(){menu.hidden=true;menuButton.setAttribute('aria-expanded','false');menuButton.setAttribute('aria-label','Открыть меню');}
menuButton.addEventListener('click',()=>{const open=menuButton.getAttribute('aria-expanded')==='true';menuButton.setAttribute('aria-expanded',String(!open));menuButton.setAttribute('aria-label',open?'Открыть меню':'Закрыть меню');menu.hidden=open;});
menu.querySelectorAll('a').forEach(link=>link.addEventListener('click',closeMenu));
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!menu.hidden){closeMenu();menuButton.focus();}});
window.matchMedia('(min-width:901px)').addEventListener('change',event=>{if(event.matches)closeMenu();});
const form=document.querySelector('#request-form');
const result=document.querySelector('#request-result');
const output=document.querySelector('#prepared-text');
const copyStatus=document.querySelector('#copy-status');
document.querySelectorAll('[data-type]').forEach(link=>{link.addEventListener('click',()=>{document.querySelectorAll('input[name="type"]').forEach(input=>{input.checked=input.value===link.dataset.type;});form.hidden=false;result.hidden=true;});});
form.addEventListener('submit',event=>{
  event.preventDefault();
  const symptoms=form.elements.symptoms.value.trim();
  if(!symptoms){form.elements.symptoms.setCustomValidity('Опишите неисправность.');form.elements.symptoms.reportValidity();return;}
  form.elements.symptoms.setCustomValidity('');
  const model=form.elements.model.value.trim();
  const location=form.elements.location.value.trim();
  output.value=['Здравствуйте, «Гараж 61»! Хочу обсудить ремонт.',`Техника: ${form.elements.type.value}.`,model?`Марка / модель / год: ${model}.`:'',location?`Местонахождение техники: ${location}.`:'',`Описание задачи: ${symptoms}`,'','Подскажите, какие данные ещё понадобятся для оценки работ.'].filter((line,index)=>line||index>3).join('\n');
  form.hidden=true;result.hidden=false;copyStatus.textContent='';document.querySelector('#copy-request').textContent='Скопировать текст';output.focus({preventScroll:true});
});
form.elements.symptoms.addEventListener('input',()=>form.elements.symptoms.setCustomValidity(''));
document.querySelector('#edit-request').addEventListener('click',()=>{result.hidden=true;form.hidden=false;form.elements.symptoms.focus({preventScroll:true});});
document.querySelector('#copy-request').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText(output.value);copyStatus.textContent='Скопировано. Передайте текст мастеру удобным способом.';document.querySelector('#copy-request').textContent='Текст скопирован ✓';}
  catch{output.focus();output.select();copyStatus.textContent='Текст выделен. Скопируйте его через меню устройства.';}
});
