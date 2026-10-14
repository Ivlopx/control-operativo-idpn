// Same-origin mutations carry the current session's CSRF token.
(() => {
  const original = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = (options.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    if (url.origin === location.origin && !['GET','HEAD','OPTIONS'].includes(method)) {
      const headers = new Headers(options.headers || (typeof input !== 'string' && input.headers) || {});
      headers.set('X-CSRF-Token',document.querySelector('meta[name="csrf-token"]')?.content || '');
      options = {...options,headers};
    }
    return original(input,options).then(response => {
      if (response.status === 401 && url.pathname !== '/login') location.assign('/login');
      return response;
    });
  };
})();

// Native details keep the account menu accessible to keyboard users.
document.addEventListener('click',event=>{
  document.querySelectorAll('.account-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});
});
document.addEventListener('keydown',event=>{
  if(event.key!=='Escape')return;
  document.querySelectorAll('.account-menu[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary').focus();});
});
