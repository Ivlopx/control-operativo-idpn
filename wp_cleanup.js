// Runs inside the WordPress editor. Both removal workflows use this function.
async options => {
    const verify = !!(options && options.verify);
    const isCognito = value => /(?:cognitoforms\.com|\bcog-(?:cognito|form)\b|\bcognito\s*\.\s*(?:load|embed)\s*\(|\[cognito(?:forms)?\b|data-idpn-component\s*=\s*["']cognito["']|class\s*=\s*["'][^"']*\bcognito(?:-form)?\b)/i.test(value);
    const isFolio = value => /(?:\bfolio\s*:|data-idpn-component\s*=\s*["']folio["'])/i.test(value);
    const stats = { removedCognito: false, removedFolio: false, cognitoCount: 0, folioCount: 0 };
    const countBlocks = blocks => blocks.reduce((n, b) => n + 1 + countBlocks(b.innerBlocks || []), 0);
    const mark = kind => {
        stats[kind === 'cognito' ? 'removedCognito' : 'removedFolio'] = true;
        stats[kind === 'cognito' ? 'cognitoCount' : 'folioCount']++;
    };
    const cleanManualHTML = (root, doc) => {
        // Cognito can be a script, iframe, or its own wrapper. Never remove all iframes.
        for (const node of root.querySelectorAll('script, iframe, .cognito, .cognito-form, .cog-cognito, .cog-form, [data-idpn-component="cognito"]')) {
            if (!root.contains(node)) continue;
            if (node.closest('[data-idpn-page="1"]')) continue;
            if (isCognito(node.outerHTML)) {
                node.remove();
                mark('cognito');
            }
        }
        for (const node of root.querySelectorAll('h1,h2,h3,h4,h5,h6,p')) {
            if (root.contains(node) && !node.closest('[data-idpn-page="1"]') && isFolio(node.textContent)) {
                node.remove();
                mark('folio');
            }
        }
        const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const texts = [];
        while (walker.nextNode()) texts.push(walker.currentNode);
        for (const node of texts) {
            if (node.parentElement.closest('[data-idpn-page="1"]')) continue;
            if (/\[cognito(?:forms)?\b/i.test(node.nodeValue)) {
                node.nodeValue = node.nodeValue.replace(/\[cognito(?:forms)?\b[^\]]*\](?:\s*\[\/cognito(?:forms)?\])?/gi, '');
                mark('cognito');
            }
        }
        // If an unfamiliar embed remains, stop rather than deleting unrelated content.
        const legacy = root.cloneNode(true);
        for (const owned of legacy.querySelectorAll('[data-idpn-page="1"]')) owned.remove();
        if (isCognito(legacy.innerHTML) || isFolio(legacy.textContent)) {
            throw new Error('Contenido Cognito/folio no reconocido: se necesita revisar este bloque antes de modificarlo.');
        }
    };
    const cleanHTML = html => {
        const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
        const root = doc.body;
        // Our page contract is independent of text, order, and the embed provider.
        for (const node of root.querySelectorAll('[data-idpn-page="1"] [data-idpn-component="cognito"], [data-idpn-page="1"] [data-idpn-component="folio"]')) {
            if (!root.contains(node)) continue;
            mark(node.getAttribute('data-idpn-component'));
            node.remove();
        }
        cleanManualHTML(root, doc);
        return root.innerHTML;
    };
    const cleanBlocks = blocks => blocks.flatMap(block => {
        const children = block.innerBlocks || [];
        if (block.name === 'core/block' && block.attributes && block.attributes.ref) {
            throw new Error('La página contiene un patrón sincronizado. Se requiere revisar su contenido referenciado antes de confirmar la limpieza.');
        }
        // The submitted resource is not the separate Cognito approval form.
        if (block.name === 'core/embed' && (block.attributes?.className || '').split(/\s+/).includes('idpn-primary-form-resource')) {
            return [{ ...block, innerBlocks: cleanBlocks(children) }];
        }
        // Inspect this block's own data, so a group is not deleted with its document.
        const strings = value => typeof value === 'string' ? [value] : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];
        // Valid containers serialize their children separately; inspect each child once.
        const serialized = strings([block.originalContent, block.innerHTML]).join('\n');
        if (children.length && block.isValid === false && (isCognito(serialized) || isFolio(serialized))) {
            throw new Error('El contenedor tiene HTML inválido. Revisa ese bloque antes de retirar Cognito y folio.');
        }
        const own = strings([block.name, block.attributes, ...(children.length ? [] : [serialized])]).join('\n');
        const cognito = isCognito(own) || /(?:^|\/)(?:cognito[-_]?forms?|cog-form)(?:\/|$)/i.test(block.name);
        const folio = isFolio(own);
        if (cognito || folio) {
            const content = block.attributes && block.attributes.content;
            if (['core/html', 'core/freeform'].includes(block.name) && typeof content === 'string') {
                const cleaned = cleanHTML(content);
                if (!cleaned.replace(/<!--[^]*?-->/g, '').trim()) return [];
                return [{ ...block, originalContent: cleaned, innerHTML: cleaned,
                    attributes: { ...block.attributes, content: cleaned }, innerBlocks: cleanBlocks(children) }];
            }
            if (children.length) {
                throw new Error(`Cognito/folio en un contenedor no reconocido (${block.name}). No se eliminó el contenedor.`);
            }
            if (cognito && /officeapps\.live\.com|embedpress|\.(?:pdf|docx?|xlsx?|pptx?)(?:["?\s]|$)/i.test(own)) {
                throw new Error(`El bloque ${block.name} mezcla Cognito con un documento. Se requiere revisar su estructura.`);
            }
            if (cognito) mark('cognito');
            if (folio) mark('folio');
            return [];
        }
        return [{ ...block, innerBlocks: cleanBlocks(children) }];
    });
    try {
        const select = wp.data.select('core/block-editor');
        const dispatch = wp.data.dispatch('core/block-editor');
        const editor = wp.data.select('core/editor');
        if (!select || !dispatch || !editor || !wp.blocks) throw new Error('API de Gutenberg no disponible.');
        let blocks;
        let record;
        if (verify) {
            if (typeof wp.apiFetch !== 'function') throw new Error('No se puede verificar el guardado: wp.apiFetch no disponible.');
            const id = editor.getCurrentPostId();
            const type = editor.getCurrentPostType();
            const entity = wp.data.select('core').getEntityConfig('postType', type);
            if (!id || !entity || !entity.baseURL) throw new Error('No se encontró la ruta REST de la página.');
            record = await wp.apiFetch({ path: `${entity.baseURL}/${id}?context=edit`, cache: 'no-store' });
            if (!record.content || typeof record.content.raw !== 'string') throw new Error('WordPress no devolvió el contenido guardado.');
            blocks = wp.blocks.parse(record.content.raw);
        } else {
            blocks = select.getBlocks();
        }
        const cleaned = cleanBlocks(blocks);
        if (verify && (stats.removedCognito || stats.removedFolio)) {
            throw new Error('La verificación encontró Cognito o el folio todavía presentes en el contenido guardado.');
        }
        if (!verify && (stats.removedCognito || stats.removedFolio)) dispatch.resetBlocks(cleaned);
        return { success: true, ...stats, originalCount: countBlocks(blocks), keptCount: countBlocks(cleaned), url: record && record.link };
    } catch (error) {
        return { success: false, reason: error.message || String(error) };
    }
}
