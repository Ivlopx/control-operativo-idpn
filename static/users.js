(() => {
  const el = id => document.getElementById(id);
  const node = (tag, text, className) => { const result=document.createElement(tag);if(text!==undefined)result.textContent=text;if(className)result.className=className;return result; };
  const icon = name => {const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','ui-icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#icon-'+name);svg.append(use);return svg;};
  const message = (text,error=false) => {el('users-message').textContent=text;el('users-message').hidden=false;el('users-message').className=error?'account-notice account-notice-error':'account-notice';};
  async function api(url,options={}) {const response=await fetch(url,options),data=await response.json();if(!response.ok)throw new Error(data.error||'No se pudo guardar.');return data;}
  const input = (type,value,label) => {const field=node('input');field.type=type;field.setAttribute('aria-label',label);if(type==='checkbox')field.checked=!!value;else field.value=value||'';return field;};
  function labelled(label,field) {const group=node('div',undefined,'form-group');const caption=node('label',label);caption.htmlFor=field.id;group.append(caption,field);return group;}
  function toggle(label,field) {field.setAttribute('role','switch');const wrapper=node('label',undefined,'permission-toggle');wrapper.htmlFor=field.id;wrapper.append(field,node('span',undefined,'permission-switch'),node('span',label));wrapper.querySelector('.permission-switch').setAttribute('aria-hidden','true');return wrapper;}
  async function load() {
    const users=await api('/api/users');el('users-list').replaceChildren();
    el('users-total').textContent=users.length;el('users-active').textContent=users.filter(user=>user.active).length;el('users-admins').textContent=users.filter(user=>user.admin).length;
    users.forEach(user=>{
      const own=user.id===window.idpnUser?.id;
      const card=node('article',undefined,'user-card');
      const header=node('div',undefined,'user-card-header'),identity=node('div',undefined,'user-card-identity');
      const avatar=node('span',undefined,'account-avatar');avatar.append(icon('user'));
      const heading=node('div');heading.append(node('h3',user.username));heading.append(node('span',own?'Tu cuenta':(user.admin?'Administrador':'Usuario'),'user-card-caption'));
      identity.append(avatar,heading);header.append(identity,node('span',user.active?'Activo':'Inactivo','user-status '+(user.active?'is-active':'is-inactive')));card.append(header);
      const fields=node('div',undefined,'user-edit-fields');
      const name=input('text',user.username,'Usuario');name.id='username-'+user.id;name.maxLength=64;
      const role=node('select');role.id='role-'+user.id;role.setAttribute('aria-label','Rol de '+user.username);
      [['operator','Usuario'],['admin','Administrador']].forEach(([value,label])=>{const option=node('option',label);option.value=value;role.append(option);});role.value=user.admin?'admin':'operator';role.disabled=own;
      fields.append(labelled('Usuario',name),labelled('Rol',role));card.append(fields);
      const edit=input('checkbox',user.can_edit_companies,'Puede editar empresas: '+user.username),active=input('checkbox',user.active,'Activo: '+user.username);
      edit.id='edit-'+user.id;active.id='active-'+user.id;active.disabled=own;
      function refreshPermission(){edit.disabled=role.value==='admin';if(edit.disabled)edit.checked=true;}
      role.addEventListener('change',refreshPermission);refreshPermission();
      const permissions=node('div',undefined,'user-permissions');permissions.append(toggle('Editar empresas',edit),toggle('Cuenta activa',active));card.append(permissions);
      const password=input('password','','Restablecer contraseña: '+user.username);password.id='password-'+user.id;password.autocomplete='new-password';password.placeholder='Al menos 12 caracteres';password.maxLength=256;
      const footer=node('div',undefined,'user-card-footer');
      if(own){const link=node('a',undefined,'user-password-link');link.href='/cuenta';link.append(icon('key'),node('span','Cambiar contraseña'));footer.append(link);}
      else{const details=node('details',undefined,'user-password-reset');const summary=node('summary');summary.append(icon('key'),node('span','Restablecer contraseña'),icon('chevron'));details.append(summary,labelled('Nueva contraseña inicial',password));card.append(details);}
      const button=node('button',undefined,'btn btn-secondary user-save');button.type='button';button.append(icon('save'),node('span','Guardar'));
      button.addEventListener('click',async()=>{button.disabled=true;message('Guardando usuario…');try{await api('/api/users/'+user.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:name.value,admin:role.value==='admin',can_edit_companies:edit.checked,active:active.checked,password:password.value})});password.value='';await load();message('Usuario actualizado.');}catch(error){message(error.message,true);}finally{button.disabled=false;}});
      footer.append(button);card.append(footer);el('users-list').append(card);
      if(own){document.querySelectorAll('.account-username').forEach(label=>{label.textContent=user.username;if(label.hasAttribute('title'))label.title=user.username;});document.querySelector('.account-menu > summary').setAttribute('aria-label','Menú de '+user.username);}
    });
  }
  const role=el('new-user-role'),edit=el('new-user-edit');
  role.addEventListener('change',()=>{edit.disabled=role.value==='admin';edit.checked=edit.disabled;});
  el('new-user-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api('/api/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:el('new-username').value,password:el('new-user-password').value,admin:role.value==='admin',can_edit_companies:edit.checked})});event.target.reset();edit.disabled=false;message('Usuario creado. Cambiará su contraseña al iniciar sesión.');await load();}catch(error){message(error.message,true);}finally{button.disabled=false;}});
  load().catch(error=>message(error.message,true));
})();
