# --------------------------------------------------------------------------
    # CRUCIALE FIX: Alle non-Python dollartekens zijn $$ om ValueError te voorkomen
    # --------------------------------------------------------------------------
    html_template = Template("""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body { margin: 0; padding: 5px 0; font-family: sans-serif; background: transparent; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; width: 100%; }
            .tile { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: transform 0.2s; display: flex; flex-direction: column; align-items: center; overflow: hidden; }
            .tile:hover { transform: translateY(-3px); border-color: #1a73e8; }
            .img-container { width: 100%; height: 180px; background-color: #f5f5f5; display: flex; align-items: center; justify-content: center; overflow: hidden; }
            .img-container img { width: 100%; height: 100%; object-fit: cover; }
            .tile-caption { padding: 10px 8px; font-size: 12px; font-weight: 600; color: #202124; text-align: center; width: 100%; box-sizing: border-box; }
        </style>
    </head>
    <body>
        <div class="grid-container" id="tile-grid"></div>
        <script>
            const tegels = $tegels_json;
            const alleDossiers = $alle_dossiers_json;

            function getImageUrl(fileId) { return "https://lh3.googleusercontent.com/d/" + fileId; }
            function getFallbackUrl(fileId) { return "https://drive.google.com/thumbnail?id=" + fileId + "&sz=w1600"; }

            function renderTiles() {
                const grid = document.getElementById('tile-grid');
                grid.innerHTML = '';
                tegels.forEach((item) => {
                    const tile = document.createElement('div');
                    tile.className = 'tile';
                    tile.onclick = () => openDriveOverlay(item.doc_id);
                    tile.innerHTML = `
                        <div class="img-container">
                            <img src="$${getImageUrl(item.id)}" onerror="this.onerror=null; this.src='$${getFallbackUrl(item.id)}';" loading="lazy" />
                        </div>
                        <div class="tile-caption">$${item.display_label || item.doc_id}</div>
                    `;
                    grid.appendChild(tile);
                });
            }

            function openDriveOverlay(docId) {
                const topDoc = window.top.document;
                const dossierPaginas = alleDossiers[docId] || [];
                let currentIndex = 0;

                let scale = 1;
                let pointX = 0;
                let pointY = 0;
                let isDragging = false;
                let startX = 0;
                let startY = 0;

                const modal = topDoc.createElement('div');
                modal.id = 'rbc-drive-modal';
                modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background-color: rgba(0,0,0,0.92); z-index: 9999999; display: flex; flex-direction: column; font-family: sans-serif; user-select: none;`;

                modal.innerHTML = `
                    <div style="height: 50px; background: #141414; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; color: white; flex-shrink: 0; z-index: 10;">
                        <div style="display: flex; align-items: center;">
                            <button id="rbc-close-btn" style="background: transparent; border: none; color: white; font-size: 24px; cursor: pointer; padding: 5px 10px; margin-right: 15px;">✕</button>
                            <div id="rbc-title-info" style="font-size: 15px; font-weight: 500;">Laden...</div>
                        </div>
                        <div id="rbc-zoom-controls" style="display: flex; gap: 10px; align-items: center;">
                            <button id="rbc-reset-zoom" style="background: #333; border: 1px solid #555; color: white; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px;">Reset Zoom</button>
                        </div>
                    </div>
                    <div id="rbc-content-body" style="position: relative; flex: 1; overflow: hidden; display: flex; align-items: center; justify-content: center; cursor: grab;">
                    </div>
                `;

                topDoc.body.appendChild(modal);
                topDoc.body.style.overflow = 'hidden';

                function resetTransform() {
                    scale = 1;
                    pointX = 0;
                    pointY = 0;
                    applyTransform();
                }

                function applyTransform() {
                    const img = topDoc.getElementById('rbc-img');
                    if (img) {
                        img.style.transform = `translate($${pointX}px, $${pointY}px) scale($${scale})`;
                    }
                }

                function setupPanAndZoom(container, img) {
                    container.onwheel = function(e) {
                        e.preventDefault();
                        const oldScale = scale;
                        
                        const delta = -e.deltaY;
                        if (delta > 0) {
                            scale *= 1.15;
                        } else {
                            scale /= 1.15;
                        }

                        scale = Math.min(Math.max(0.8, scale), 8);

                        const factor = scale / oldScale;
                        pointX *= factor;
                        pointY *= factor;

                        applyTransform();
                    };

                    container.onmousedown = function(e) {
                        if (e.target.tagName === 'BUTTON' || e.target.id === 'rbc-prev-btn' || e.target.id === 'rbc-next-btn') return;
                        e.preventDefault();
                        isDragging = true;
                        startX = e.clientX - pointX;
                        startY = e.clientY - pointY;
                        container.style.cursor = 'grabbing';
                    };

                    topDoc.onmousemove = function(e) {
                        if (!isDragging) return;
                        e.preventDefault();
                        pointX = e.clientX - startX;
                        pointY = e.clientY - startY;
                        applyTransform();
                    };

                    topDoc.onmouseup = function() {
                        if (isDragging) {
                            isDragging = false;
                            container.style.cursor = 'grab';
                        }
                    };
                }

                function updateViewer() {
                    resetTransform();
                    const item = dossierPaginas[currentIndex];
                    const container = topDoc.getElementById('rbc-content-body');
                    const zoomControls = topDoc.getElementById('rbc-zoom-controls');
                    const isPdf = item.naam.toLowerCase().endsWith('.pdf') || (item.mime && item.mime.includes('pdf'));

                    topDoc.getElementById('rbc-title-info').innerText = `$${item.naam} ($${currentIndex + 1}/$${dossierPaginas.length})`;

                    if (isPdf) {
                        zoomControls.style.display = 'none';
                        container.innerHTML = `
                            <iframe src="https://drive.google.com/file/d/$${item.id}/preview" 
                                    style="width: 100%; height: 100%; border: none; background: #fff;">
                            </iframe>
                        `;
                    } else {
                        zoomControls.style.display = 'flex';
                        container.innerHTML = `
                            <div id="rbc-img-wrapper" style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden;">
                                <img id="rbc-img" 
                                     style="max-width: 95vw; max-height: 90vh; object-fit: contain; transform-origin: center center; box-shadow: 0 4px 25px rgba(0,0,0,0.6);" 
                                     src="$${getImageUrl(item.id)}" 
                                     onerror="this.onerror=null; this.src='$${getFallbackUrl(item.id)}';" />
                            </div>
                            <div id="rbc-prev-btn" style="position: fixed; left: 20px; top: 50%; transform: translateY(-50%); font-size: 36px; color: white; cursor: pointer; user-select: none; background: rgba(0,0,0,0.5); padding: 8px 16px; border-radius: 50%; z-index: 20;">‹</div>
                            <div id="rbc-next-btn" style="position: fixed; right: 20px; top: 50%; transform: translateY(-50%); font-size: 36px; color: white; cursor: pointer; user-select: none; background: rgba(0,0,0,0.5); padding: 8px 16px; border-radius: 50%; z-index: 20;">›</div>
                        `;

                        const img = topDoc.getElementById('rbc-img');
                        setupPanAndZoom(container, img);

                        topDoc.getElementById('rbc-prev-btn').onclick = (e) => { e.stopPropagation(); if (currentIndex > 0) { currentIndex--; updateViewer(); } };
                        topDoc.getElementById('rbc-next-btn').onclick = (e) => { e.stopPropagation(); if (currentIndex < dossierPaginas.length - 1) { currentIndex++; updateViewer(); } };
                        topDoc.getElementById('rbc-reset-zoom').onclick = () => resetTransform();
                    }
                }

                function sluitModal() { 
                    modal.remove(); 
                    topDoc.body.style.overflow = 'auto'; 
                    topDoc.onmousemove = null;
                    topDoc.onmouseup = null;
                }

                topDoc.getElementById('rbc-close-btn').onclick = sluitModal;

                updateViewer();
            }
            renderTiles();
        </script>
    </body>
    </html>
    """)

    grid_html = html_template.substitute(
        tegels_json=json.dumps(tegel_items),
        alle_dossiers_json=json.dumps(dossiers_dict)
    )
