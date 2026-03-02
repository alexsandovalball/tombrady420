// Tom Brady Menu - Full Bot-like WebApp Interface
(function () {
    'use strict';

    // Configuration constants
    const CONFIG = {
        MIN_SEARCH_LENGTH: 2,
        MAX_CARD_CAPTION_LENGTH: 47,
        ITEMS_PER_PAGE: 5,
        MAX_SEARCH_PRODUCTS: 500,
        SEARCH_BATCH_SIZE: 50,
        LOG_BATCH_SIZE: 10,
        LOG_BATCH_INTERVAL: 30000,  // 30 seconds
        VIDEO_THUMBNAIL_SEEK_TIME: 1,  // 1 second
        VIDEO_THUMBNAIL_SEEK_PERCENT: 0.1,  // 10% of duration
        VIDEO_BOUNDS_BUFFER: 0.1,  // Buffer to stay within video bounds
        VIDEO_LOAD_TIMEOUT_MS: 15000,  // Timeout before showing tap-to-play for slow videos
        USER_DATA_CHECK_INTERVALS: [300, 700, 1500],  // Retry intervals in ms for user data loading
        SEARCH_DEBOUNCE_MS: 400,  // Debounce time for search input
        WEBAPP_CLOSE_DELAY_MS: 1500,  // Delay before closing webapp after successful product send
        ERROR_DISPLAY_MS: 4000,  // Duration to show error messages on buttons
        INITIAL_DATA_TIMEOUT_MS: 10000,  // Timeout for initial data load to prevent infinite loading
        MAX_CACHE_SIZE: 20  // Maximum number of cached API responses to prevent memory leaks
    };

    const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : {
        initData: "",
        initDataUnsafe: { user: null },
        platform: "Web",
        themeParams: {},
        ready: () => {},
        expand: () => {},
        close: () => {},
        BackButton: { show: () => {}, hide: () => {}, onClick: () => {} }
    };

    // WebApp Logger for comprehensive logging
    class WebAppLogger {
        constructor() {
            this.logs = [];
            this.sessionId = this.generateSessionId();
            this.batchTimer = null;
            this.startBatchTimer();
        }

        generateSessionId() {
            return `webapp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        }

        getUserInfo() {
            const user = tg.initDataUnsafe?.user;
            if (user) {
                return {
                    user_id: user.id,
                    username: user.username || `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'unknown',
                    first_name: user.first_name || '',
                    last_name: user.last_name || ''
                };
            }
            return {
                user_id: 'unknown',
                username: 'unknown',
                first_name: '',
                last_name: ''
            };
        }

        log(event_type, action, status = 'success', error = '', details = {}) {
            const userInfo = this.getUserInfo();
            const logEntry = {
                timestamp: new Date().toISOString(),
                session_id: this.sessionId,
                user_id: userInfo.user_id,
                username: userInfo.username,
                event_type,
                action,
                status,
                error,
                details
            };

            this.logs.push(logEntry);

            // Send batch if we've hit the limit
            if (this.logs.length >= CONFIG.LOG_BATCH_SIZE) {
                this.sendLogs();
            }
        }

        startBatchTimer() {
            if (this.batchTimer) {
                clearInterval(this.batchTimer);
            }
            this.batchTimer = setInterval(() => {
                if (this.logs.length > 0) {
                    this.sendLogs();
                }
            }, CONFIG.LOG_BATCH_INTERVAL);
        }

        cleanup() {
            if (this.batchTimer) {
                clearInterval(this.batchTimer);
                this.batchTimer = null;
            }
            // Send any remaining logs before cleanup
            if (this.logs.length > 0) {
                this.sendLogs();
            }
        }

        async sendLogs() {
            if (this.logs.length === 0) return;

            const logsToSend = [...this.logs];
            this.logs = [];

            try {
                const headers = { 'Content-Type': 'application/json' };
                if (tg.initData) {
                    headers['X-Telegram-Init-Data'] = tg.initData;
                }

                const response = await fetch('/api/webapp-logs', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ logs: logsToSend })
                });

                if (!response.ok) {
                    // Re-add logs to queue on failure (but don't let it grow infinitely)
                    if (this.logs.length < 100) {
                        this.logs = [...logsToSend, ...this.logs];
                    }
                }
            } catch (err) {
                // Re-add logs to queue on failure
                if (this.logs.length < 100) {
                    this.logs = [...logsToSend, ...this.logs];
                }
            }
        }

        // Log specific event types
        userInteraction(action, details = {}) {
            this.log('user_interaction', action, 'success', '', details);
        }

        apiCall(action, status, details = {}) {
            this.log('api_call', action, status, '', details);
        }

        videoEvent(action, status, error = '', details = {}) {
            this.log('video_playback', action, status, error, details);
        }

        navigationChange(action, details = {}) {
            this.log('navigation', action, 'success', '', details);
        }

        error(action, error, details = {}) {
            this.log('error', action, 'error', error, details);
        }
    }

    // Initialize logger
    const logger = new WebAppLogger();

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        logger.cleanup();
    });

    // MediaRequestManager for intelligent request deduplication and throttling
    class MediaRequestManager {
        constructor() {
            this.activeRequests = new Map(); // Key: "productId_mediaIndex", Value: Promise
            this.requestQueue = []; // Queue of pending requests
            this.cache = new Map(); // In-memory cache: Key: "productId_mediaIndex", Value: { data, timestamp }
            this.maxConcurrent = 6; // Maximum concurrent requests (increased from 3 for faster loading)
            this.currentConcurrent = 0;
            this.cacheTTL = 300000; // 5 minutes TTL for cache
            this.retryDelays = [1000, 2000, 4000]; // Exponential backoff delays in ms
        }

        /**
         * Get cache key for a media request
         */
        getCacheKey(productId, mediaIndex) {
            return `${productId}_${mediaIndex}`;
        }

        /**
         * Check if cached data is still valid
         */
        isCacheValid(cacheEntry) {
            if (!cacheEntry) return false;
            return (Date.now() - cacheEntry.timestamp) < this.cacheTTL;
        }

        /**
         * Get data from cache if valid
         */
        getFromCache(productId, mediaIndex) {
            const key = this.getCacheKey(productId, mediaIndex);
            const cached = this.cache.get(key);
            if (this.isCacheValid(cached)) {
                logger.log('media_cache', 'cache_hit', 'success', '', {
                    product_id: productId,
                    media_index: mediaIndex
                });
                return cached.data;
            }
            return null;
        }

        /**
         * Store data in cache
         */
        storeInCache(productId, mediaIndex, data) {
            const key = this.getCacheKey(productId, mediaIndex);
            this.cache.set(key, {
                data: data,
                timestamp: Date.now()
            });

            // Cleanup old cache entries (LRU-like)
            if (this.cache.size > CONFIG.MAX_CACHE_SIZE) {
                const firstKey = this.cache.keys().next().value;
                this.cache.delete(firstKey);
            }

            logger.log('media_cache', 'cache_store', 'success', '', {
                product_id: productId,
                media_index: mediaIndex,
                cache_size: this.cache.size
            });
        }

        /**
         * Fetch media with retry logic and exponential backoff
         * Only retries on transient errors (5xx and network failures)
         * Does NOT retry on permanent errors like 413 and 404
         */
        async fetchWithRetry(url, productId, mediaIndex, retryCount = 0) {
            try {
                const response = await fetch(url);
                
                // Check for permanent errors that should not be retried
                if (response.status === 413 || response.status === 404) {
                    // These are permanent errors - don't retry
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                if (!response.ok) {
                    // Only retry on 5xx errors (server errors)
                    if (response.status >= 500 && response.status < 600 && retryCount < this.retryDelays.length) {
                        const delay = this.retryDelays[retryCount];
                        logger.log('media_request', 'retry', 'warning', `HTTP ${response.status}`, {
                            product_id: productId,
                            media_index: mediaIndex,
                            retry_count: retryCount,
                            delay: delay,
                            status: response.status
                        });

                        await new Promise(resolve => setTimeout(resolve, delay));
                        return this.fetchWithRetry(url, productId, mediaIndex, retryCount + 1);
                    }
                    
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                // For videos, return the blob URL
                if (url.includes('/media/')) {
                    const blob = await response.blob();
                    const blobUrl = URL.createObjectURL(blob);
                    return blobUrl;
                }

                return response;

            } catch (error) {
                // Only retry on network failures (not HTTP errors)
                // Check if it's a network error (no HTTP status in message)
                const isNetworkError = !error.message.match(/HTTP \d{3}/);
                
                if (isNetworkError && retryCount < this.retryDelays.length) {
                    const delay = this.retryDelays[retryCount];
                    logger.log('media_request', 'retry', 'warning', error.message, {
                        product_id: productId,
                        media_index: mediaIndex,
                        retry_count: retryCount,
                        delay: delay
                    });

                    await new Promise(resolve => setTimeout(resolve, delay));
                    return this.fetchWithRetry(url, productId, mediaIndex, retryCount + 1);
                }

                throw error;
            }
        }

        /**
         * Process queued requests when slots become available
         */
        processQueue() {
            while (this.requestQueue.length > 0 && this.currentConcurrent < this.maxConcurrent) {
                const { url, productId, mediaIndex, resolve, reject } = this.requestQueue.shift();
                this.executeRequest(url, productId, mediaIndex, resolve, reject);
            }
        }

        /**
         * Execute a single request
         */
        async executeRequest(url, productId, mediaIndex, resolve, reject) {
            const key = this.getCacheKey(productId, mediaIndex);
            this.currentConcurrent++;

            logger.log('media_request', 'request_start', 'info', '', {
                product_id: productId,
                media_index: mediaIndex,
                concurrent_count: this.currentConcurrent,
                queue_length: this.requestQueue.length
            });

            try {
                const result = await this.fetchWithRetry(url, productId, mediaIndex);
                
                // Store in cache
                this.storeInCache(productId, mediaIndex, result);

                logger.log('media_request', 'request_success', 'success', '', {
                    product_id: productId,
                    media_index: mediaIndex
                });

                resolve(result);
            } catch (error) {
                logger.log('media_request', 'request_error', 'error', error.message, {
                    product_id: productId,
                    media_index: mediaIndex
                });

                reject(error);
            } finally {
                this.currentConcurrent--;
                this.activeRequests.delete(key);
                
                // Process next queued request
                this.processQueue();
            }
        }

        /**
         * Request media with deduplication, throttling, and caching
         */
        async request(url, productId, mediaIndex) {
            // Check cache first
            const cached = this.getFromCache(productId, mediaIndex);
            if (cached) {
                return cached;
            }

            const key = this.getCacheKey(productId, mediaIndex);

            // If request is already in progress, return the existing promise
            if (this.activeRequests.has(key)) {
                logger.log('media_request', 'deduplication', 'info', '', {
                    product_id: productId,
                    media_index: mediaIndex
                });
                return this.activeRequests.get(key);
            }

            // Create new request promise
            const requestPromise = new Promise((resolve, reject) => {
                if (this.currentConcurrent < this.maxConcurrent) {
                    // Execute immediately
                    this.executeRequest(url, productId, mediaIndex, resolve, reject);
                } else {
                    // Add to queue
                    this.requestQueue.push({ url, productId, mediaIndex, resolve, reject });
                    logger.log('media_request', 'queued', 'info', '', {
                        product_id: productId,
                        media_index: mediaIndex,
                        queue_position: this.requestQueue.length
                    });
                }
            });

            this.activeRequests.set(key, requestPromise);
            return requestPromise;
        }

        /**
         * Cancel all pending requests for cleanup
         */
        cancelAll() {
            // Clear queue
            this.requestQueue = [];
            
            // Note: We can't actually cancel fetch requests, but we can clear our tracking
            this.activeRequests.clear();
            
            logger.log('media_request', 'cancel_all', 'info', '', {
                queue_cleared: true
            });
        }

        /**
         * Clear cache
         */
        clearCache() {
            this.cache.clear();
            logger.log('media_cache', 'cache_clear', 'success', '', {});
        }
    }

    // Initialize media request manager
    const mediaRequestManager = new MediaRequestManager();

    // Debounce utility: delays invoking func until after wait ms have elapsed
    // since the last call, preventing excessive calls during rapid input events.
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // App state
    const state = {
        view: 'welcome',  // 'welcome', 'language', 'search', 'categories', 'products', 'product-detail'
        currentCategory: null,
        currentSubcategory: null,
        currentProduct: null,  // For product detail view
        searchQuery: null,
        page: 0,
        itemsPerPage: CONFIG.ITEMS_PER_PAGE,
        categories: [],
        products: [],
        searchResults: [],
        totalProducts: 0,
        orderContact: null,
        allProductsCount: 0,
        userLanguage: 'en',
        isSubscribed: true,
        userId: null,
        username: null,  // Store username for display
        botUsername: null,  // Store bot username for deep links
        lastLoggedUserStateId: null,  // Track last logged user state ID to avoid duplicate logging
        history: [],  // Navigation history for back button
        languages: [
            { code: 'en', name: 'English (USA)', flag: '🇺🇸' },
            { code: 'de', name: 'German', flag: '🇩🇪' },
            { code: 'nl', name: 'Dutch', flag: '🇳🇱' },
            { code: 'it', name: 'Italian', flag: '🇮🇹' },
            { code: 'es', name: 'Spanish', flag: '🇪🇸' },
            { code: 'fr', name: 'French', flag: '🇫🇷' }
        ],
        cache: {},  // Simple cache for API responses
        loadingInProgress: false,  // Prevent duplicate requests
        pendingRequests: new Map(),  // Track pending requests for deduplication
        initialDataLoadFailed: false  // Track if initial data load failed/timed out
    };
    
    // Create debounced pagination handler once (reused across renders)
    const debouncedPageChange = debounce(async (newPage, category, subcategory) => {
        state.page = newPage;
        // Don't reset page when paginating, and don't add to history
        await loadProducts(category, subcategory, false, false);
    }, 300);

    // Translations for different languages
    const translations = {
        en: {
            welcome_title: '👋 Welcome to Tom Brady Menu!',
            welcome_subtitle: 'Browse our complete product catalog',
            btn_view_catalog: 'SHOP',
            btn_search: 'Search Products',
            btn_language: 'Change Language',
            btn_back: '← Back',
            language_title: '🌐 Select Language',
            status_ready: 'Ready',
            status_connected: 'Connected',
            status_preview: 'Preview mode',
            status_loading: 'Loading...',
            status_searching: 'Searching...',
            status_loading_categories: 'Loading categories...',
            status_loading_products: 'Loading products...',
            status_enter_search: 'Enter search query',
            categories_title: 'Categories',
            products_title: 'Products',
            all_products: 'All Products',
            search_placeholder: 'Search products...',
            search_button: 'Search',
            search_min_chars: 'Please enter at least {0} characters',
            search_failed: 'Search failed. Please try again later.',
            search_no_results: 'No products found',
            search_results_count: 'Found {0} products - Showing {1} on this page',
            categories_count: '{0} categories',
            categories_available: '{0} categories available',
            subcategories_count: '{0} subcategories',
            subcategories_in: 'subcategories in {0}',
            all_in_category: 'All in {0}',
            items_count: '{0} items',
            no_products: 'No products available in this category',
            showing_range: 'Showing {0}-{1} of {2}',
            page_info: 'Page {0} / {1}',
            btn_prev: '◀ Prev',
            btn_next: 'Next ▶',
            home: 'Home',
            order_contact_prefix: 'To place an order, contact:',
            error_load_categories: 'Unable to load categories. Please try again later.',
            error_load_products: 'Unable to load products. Please try again later.',
            video_content: 'Video Content',
            loading_video: 'Loading video...',
            tap_to_play: 'Tap to play video',
            file_too_large: 'File Too Large for Web Playback',
            video_unavailable: 'Video Unavailable',
            image_unavailable: 'Image Unavailable',
            no_media: 'No Media',
            error_open_from_telegram: 'Please open this webapp from the Telegram bot',
            error_not_authenticated: '⚠️ Not Authenticated',
            initial_data_timeout_warning: 'Some features may be unavailable. Please check your connection and try again.',
            btn_retry_load: 'Retry',
            btn_dm_to_order: '💬 DM TO ORDER',
            language_changed: 'Language changed to {0}'
        },
        de: {
            welcome_title: '👋 Willkommen zum Tom Brady Menü!',
            welcome_subtitle: 'Durchsuchen Sie unseren kompletten Produktkatalog',
            btn_view_catalog: 'SHOP',
            btn_search: 'Produkte suchen',
            btn_language: 'Sprache ändern',
            btn_back: '← Zurück',
            language_title: '🌐 Sprache wählen',
            status_ready: 'Bereit',
            status_connected: 'Verbunden',
            status_preview: 'Vorschaumodus',
            status_loading: 'Lädt...',
            status_searching: 'Suchen...',
            status_loading_categories: 'Kategorien werden geladen...',
            status_loading_products: 'Produkte werden geladen...',
            status_enter_search: 'Suchanfrage eingeben',
            categories_title: 'Kategorien',
            products_title: 'Produkte',
            all_products: 'Alle Produkte',
            search_placeholder: 'Produkte suchen...',
            search_button: 'Suchen',
            search_min_chars: 'Bitte mindestens {0} Zeichen eingeben',
            search_failed: 'Suche fehlgeschlagen. Bitte später erneut versuchen.',
            search_no_results: 'Keine Produkte gefunden',
            search_results_count: '{0} Produkte gefunden - {1} auf dieser Seite angezeigt',
            categories_count: '{0} Kategorien',
            categories_available: '{0} Kategorien verfügbar',
            subcategories_count: '{0} Unterkategorien',
            subcategories_in: 'Unterkategorien in {0}',
            all_in_category: 'Alle in {0}',
            items_count: '{0} Artikel',
            no_products: 'Keine Produkte in dieser Kategorie verfügbar',
            showing_range: 'Zeige {0}-{1} von {2}',
            page_info: 'Seite {0} / {1}',
            btn_prev: '◀ Zurück',
            btn_next: 'Weiter ▶',
            home: 'Startseite',
            order_contact_prefix: 'Zum Bestellen kontaktieren:',
            error_load_categories: 'Kategorien können nicht geladen werden. Bitte später erneut versuchen.',
            error_load_products: 'Produkte können nicht geladen werden. Bitte später erneut versuchen.',
            video_content: 'Videoinhalt',
            loading_video: 'Video wird geladen...',
            tap_to_play: 'Tippen zum Abspielen',
            file_too_large: 'Datei zu groß für Web-Wiedergabe',
            video_unavailable: 'Video nicht verfügbar',
            image_unavailable: 'Bild nicht verfügbar',
            no_media: 'Keine Medien',
            error_open_from_telegram: 'Bitte öffnen Sie diese Webapp vom Telegram-Bot',
            error_not_authenticated: '⚠️ Nicht authentifiziert',
            initial_data_timeout_warning: 'Einige Funktionen sind möglicherweise nicht verfügbar. Bitte überprüfen Sie Ihre Verbindung und versuchen Sie es erneut.',
            btn_retry_load: 'Wiederholen',
            btn_dm_to_order: '💬 DM ZUM BESTELLEN',
            language_changed: 'Sprache geändert zu {0}'
        },
        es: {
            welcome_title: '👋 ¡Bienvenido al Menú de Tom Brady!',
            welcome_subtitle: 'Explore nuestro catálogo completo de productos',
            btn_view_catalog: 'SHOP',
            btn_search: 'Buscar Productos',
            btn_language: 'Cambiar Idioma',
            btn_back: '← Atrás',
            language_title: '🌐 Seleccionar Idioma',
            status_ready: 'Listo',
            status_connected: 'Conectado',
            status_preview: 'Modo de vista previa',
            status_loading: 'Cargando...',
            status_searching: 'Buscando...',
            status_loading_categories: 'Cargando categorías...',
            status_loading_products: 'Cargando productos...',
            status_enter_search: 'Ingrese consulta de búsqueda',
            categories_title: 'Categorías',
            products_title: 'Productos',
            all_products: 'Todos los Productos',
            search_placeholder: 'Buscar productos...',
            search_button: 'Buscar',
            search_min_chars: 'Por favor ingrese al menos {0} caracteres',
            search_failed: 'Búsqueda fallida. Por favor intente de nuevo más tarde.',
            search_no_results: 'No se encontraron productos',
            search_results_count: 'Se encontraron {0} productos - Mostrando {1} en esta página',
            categories_count: '{0} categorías',
            categories_available: '{0} categorías disponibles',
            subcategories_count: '{0} subcategorías',
            subcategories_in: 'subcategorías en {0}',
            all_in_category: 'Todo en {0}',
            items_count: '{0} artículos',
            no_products: 'No hay productos disponibles en esta categoría',
            showing_range: 'Mostrando {0}-{1} de {2}',
            page_info: 'Página {0} / {1}',
            btn_prev: '◀ Anterior',
            btn_next: 'Siguiente ▶',
            home: 'Inicio',
            order_contact_prefix: 'Para realizar un pedido, contacte:',
            error_load_categories: 'No se pueden cargar las categorías. Por favor intente de nuevo más tarde.',
            error_load_products: 'No se pueden cargar los productos. Por favor intente de nuevo más tarde.',
            video_content: 'Contenido de Video',
            loading_video: 'Cargando video...',
            tap_to_play: 'Toque para reproducir video',
            file_too_large: 'Archivo Demasiado Grande para Reproducción Web',
            video_unavailable: 'Video No Disponible',
            image_unavailable: 'Imagen No Disponible',
            no_media: 'Sin Medios',
            error_open_from_telegram: 'Por favor abra esta webapp desde el bot de Telegram',
            error_not_authenticated: '⚠️ No Autenticado',
            initial_data_timeout_warning: 'Algunas funciones pueden no estar disponibles. Por favor, verifique su conexión e intente nuevamente.',
            btn_retry_load: 'Reintentar',
            btn_dm_to_order: '💬 DM PARA ORDENAR',
            language_changed: 'Idioma cambiado a {0}'
        },
        fr: {
            welcome_title: '👋 Bienvenue au Menu Tom Brady!',
            welcome_subtitle: 'Parcourez notre catalogue complet de produits',
            btn_view_catalog: 'SHOP',
            btn_search: 'Rechercher des Produits',
            btn_language: 'Changer la Langue',
            btn_back: '← Retour',
            language_title: '🌐 Sélectionner la Langue',
            status_ready: 'Prêt',
            status_connected: 'Connecté',
            status_preview: 'Mode aperçu',
            status_loading: 'Chargement...',
            status_searching: 'Recherche...',
            status_loading_categories: 'Chargement des catégories...',
            status_loading_products: 'Chargement des produits...',
            status_enter_search: 'Entrez votre recherche',
            categories_title: 'Catégories',
            products_title: 'Produits',
            all_products: 'Tous les Produits',
            search_placeholder: 'Rechercher des produits...',
            search_button: 'Rechercher',
            search_min_chars: 'Veuillez entrer au moins {0} caractères',
            search_failed: 'Échec de la recherche. Veuillez réessayer plus tard.',
            search_no_results: 'Aucun produit trouvé',
            search_results_count: '{0} produits trouvés - Affichage de {1} sur cette page',
            categories_count: '{0} catégories',
            categories_available: '{0} catégories disponibles',
            subcategories_count: '{0} sous-catégories',
            subcategories_in: 'sous-catégories dans {0}',
            all_in_category: 'Tout dans {0}',
            items_count: '{0} articles',
            no_products: 'Aucun produit disponible dans cette catégorie',
            showing_range: 'Affichage de {0}-{1} sur {2}',
            page_info: 'Page {0} / {1}',
            btn_prev: '◀ Précédent',
            btn_next: 'Suivant ▶',
            home: 'Accueil',
            order_contact_prefix: 'Pour passer une commande, contactez:',
            error_load_categories: 'Impossible de charger les catégories. Veuillez réessayer plus tard.',
            error_load_products: 'Impossible de charger les produits. Veuillez réessayer plus tard.',
            video_content: 'Contenu Vidéo',
            loading_video: 'Chargement de la vidéo...',
            tap_to_play: 'Appuyez pour lire la vidéo',
            file_too_large: 'Fichier Trop Grand pour Lecture Web',
            video_unavailable: 'Vidéo Non Disponible',
            image_unavailable: 'Image Non Disponible',
            no_media: 'Aucun Média',
            error_open_from_telegram: 'Veuillez ouvrir cette webapp depuis le bot Telegram',
            error_not_authenticated: '⚠️ Non Authentifié',
            initial_data_timeout_warning: 'Certaines fonctionnalités peuvent être indisponibles. Veuillez vérifier votre connexion et réessayer.',
            btn_retry_load: 'Réessayer',
            btn_dm_to_order: '💬 DM POUR COMMANDER',
            language_changed: 'Langue changée en {0}'
        },
        it: {
            welcome_title: '👋 Benvenuto al Menu Tom Brady!',
            welcome_subtitle: 'Sfoglia il nostro catalogo completo di prodotti',
            btn_view_catalog: 'SHOP',
            btn_search: 'Cerca Prodotti',
            btn_language: 'Cambia Lingua',
            btn_back: '← Indietro',
            language_title: '🌐 Seleziona Lingua',
            status_ready: 'Pronto',
            status_connected: 'Connesso',
            status_preview: 'Modalità anteprima',
            status_loading: 'Caricamento...',
            status_searching: 'Ricerca...',
            status_loading_categories: 'Caricamento categorie...',
            status_loading_products: 'Caricamento prodotti...',
            status_enter_search: 'Inserisci ricerca',
            categories_title: 'Categorie',
            products_title: 'Prodotti',
            all_products: 'Tutti i Prodotti',
            search_placeholder: 'Cerca prodotti...',
            search_button: 'Cerca',
            search_min_chars: 'Inserisci almeno {0} caratteri',
            search_failed: 'Ricerca fallita. Riprova più tardi.',
            search_no_results: 'Nessun prodotto trovato',
            search_results_count: 'Trovati {0} prodotti - Mostrando {1} in questa pagina',
            categories_count: '{0} categorie',
            categories_available: '{0} categorie disponibili',
            subcategories_count: '{0} sottocategorie',
            subcategories_in: 'sottocategorie in {0}',
            all_in_category: 'Tutto in {0}',
            items_count: '{0} articoli',
            no_products: 'Nessun prodotto disponibile in questa categoria',
            showing_range: 'Mostrando {0}-{1} di {2}',
            page_info: 'Pagina {0} / {1}',
            btn_prev: '◀ Prec',
            btn_next: 'Succ ▶',
            home: 'Home',
            order_contact_prefix: 'Per effettuare un ordine, contatta:',
            error_load_categories: 'Impossibile caricare le categorie. Riprova più tardi.',
            error_load_products: 'Impossibile caricare i prodotti. Riprova più tardi.',
            video_content: 'Contenuto Video',
            loading_video: 'Caricamento video...',
            tap_to_play: 'Tocca per riprodurre video',
            file_too_large: 'File Troppo Grande per Riproduzione Web',
            video_unavailable: 'Video Non Disponibile',
            image_unavailable: 'Immagine Non Disponibile',
            no_media: 'Nessun Media',
            error_open_from_telegram: 'Apri questa webapp dal bot di Telegram',
            error_not_authenticated: '⚠️ Non Autenticato',
            initial_data_timeout_warning: 'Alcune funzionalità potrebbero non essere disponibili. Si prega di controllare la connessione e riprovare.',
            btn_retry_load: 'Riprova',
            btn_dm_to_order: '💬 DM PER ORDINARE',
            language_changed: 'Lingua cambiata in {0}'
        },
        nl: {
            welcome_title: '👋 Welkom bij Tom Brady Menu!',
            welcome_subtitle: 'Blader door onze volledige productcatalogus',
            btn_view_catalog: 'SHOP',
            btn_search: 'Producten Zoeken',
            btn_language: 'Taal Wijzigen',
            btn_back: '← Terug',
            language_title: '🌐 Selecteer Taal',
            status_ready: 'Klaar',
            status_connected: 'Verbonden',
            status_preview: 'Voorbeeldmodus',
            status_loading: 'Laden...',
            status_searching: 'Zoeken...',
            status_loading_categories: 'Categorieën laden...',
            status_loading_products: 'Producten laden...',
            status_enter_search: 'Voer zoekopdracht in',
            categories_title: 'Categorieën',
            products_title: 'Producten',
            all_products: 'Alle Producten',
            search_placeholder: 'Producten zoeken...',
            search_button: 'Zoeken',
            search_min_chars: 'Voer minimaal {0} tekens in',
            search_failed: 'Zoeken mislukt. Probeer het later opnieuw.',
            search_no_results: 'Geen producten gevonden',
            search_results_count: '{0} producten gevonden - {1} weergegeven op deze pagina',
            categories_count: '{0} categorieën',
            categories_available: '{0} categorieën beschikbaar',
            subcategories_count: '{0} subcategorieën',
            subcategories_in: 'subcategorieën in {0}',
            all_in_category: 'Alles in {0}',
            items_count: '{0} items',
            no_products: 'Geen producten beschikbaar in deze categorie',
            showing_range: '{0}-{1} van {2} weergeven',
            page_info: 'Pagina {0} / {1}',
            btn_prev: '◀ Vorige',
            btn_next: 'Volgende ▶',
            home: 'Home',
            order_contact_prefix: 'Om een bestelling te plaatsen, neem contact op:',
            error_load_categories: 'Kan categorieën niet laden. Probeer het later opnieuw.',
            error_load_products: 'Kan producten niet laden. Probeer het later opnieuw.',
            video_content: 'Video Inhoud',
            loading_video: 'Video laden...',
            tap_to_play: 'Tik om video af te spelen',
            file_too_large: 'Bestand Te Groot voor Web Afspelen',
            video_unavailable: 'Video Niet Beschikbaar',
            image_unavailable: 'Afbeelding Niet Beschikbaar',
            no_media: 'Geen Media',
            error_open_from_telegram: 'Open deze webapp vanuit de Telegram bot',
            error_not_authenticated: '⚠️ Niet Geauthenticeerd',
            initial_data_timeout_warning: 'Sommige functies zijn mogelijk niet beschikbaar. Controleer uw verbinding en probeer het opnieuw.',
            btn_retry_load: 'Opnieuw proberen',
            btn_dm_to_order: '💬 DM OM TE BESTELLEN',
            language_changed: 'Taal gewijzigd naar {0}'
        }
    };

    // Get translated string with optional parameter substitution
    // Usage: t('search_min_chars', 3) => "Please enter at least 3 characters"
    // Usage: t('showing_range', 1, 10, 50) => "Showing 1-10 of 50"
    function t(key, ...params) {
        const lang = state.userLanguage.split('-')[0]; // Use base language code
        let text = translations[lang]?.[key] || translations.en[key] || key;
        
        // Replace {0}, {1}, {2}, etc. with provided parameters
        if (params.length > 0) {
            params.forEach((param, index) => {
                text = text.replace(`{${index}}`, param);
            });
        }
        
        return text;
    }

    // Helper: Find language object by code (handles language variants)
    function findLanguageByCode(langCode) {
        const baseLang = langCode.split('-')[0];
        return state.languages.find(l => 
            l.code === langCode || l.code.startsWith(baseLang)
        );
    }

    // Helper: Normalize contact username (remove @ prefix)
    function normalizeContactUsername(contact) {
        if (!contact) return '';
        return contact.startsWith('@') ? contact.slice(1) : contact;
    }

    // Helper: Deduplicated JSON fetch to prevent duplicate API calls
    // This caches the parsed JSON data, not the Response object, to avoid
    // "Body is disturbed or locked" errors when multiple consumers call .json()
    async function fetchJsonWithDedup(url, options = {}) {
        // Create a stable key for this request
        // Use simple key based on URL and auth state to avoid JSON.stringify issues
        const hasAuth = tg.initData ? 'auth' : 'noauth';
        const key = `${url}|${hasAuth}`;

        // If there's already a pending request for this exact URL+options, return that promise
        if (state.pendingRequests.has(key)) {
            logger.log('api_call', 'deduplication', 'info', '', { url, status: 'reusing_pending' });
            return state.pendingRequests.get(key);
        }

        // Create new request that resolves to JSON data
        const requestPromise = fetch(url, options)
            .then(async response => {
                if (!response.ok) {
                    // For error responses, return the response object itself
                    // so callers can handle errors appropriately
                    return response;
                }
                // Parse JSON once and return the data
                const data = await response.json();
                // Create a mock response object with the parsed data
                return {
                    ok: true,
                    status: response.status,
                    statusText: response.statusText,
                    headers: response.headers,
                    data: data,
                    json: async () => data,  // Allow .json() to still work
                    text: async () => JSON.stringify(data)
                };
            })
            .then(result => {
                state.pendingRequests.delete(key);
                return result;
            })
            .catch(error => {
                state.pendingRequests.delete(key);
                throw error;
            });

        // Store the promise for deduplication
        state.pendingRequests.set(key, requestPromise);

        return requestPromise;
    }

    // Apply translations to the page
    function applyTranslations() {
        // Translate elements with data-i18n attribute
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translation = t(key);
            if (el.tagName === 'INPUT' && el.type === 'text') {
                el.placeholder = translation;
            } else {
                el.textContent = translation;
            }
        });
        
        // Translate placeholders with data-i18n-placeholder attribute
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            const translation = t(key);
            el.placeholder = translation;
        });
        
        // Update dynamic elements
        const langDisplay = document.getElementById('current-lang');
        if (langDisplay) {
            const lang = findLanguageByCode(state.userLanguage);
            if (lang) {
                langDisplay.textContent = lang.name;
            }
        }
        

        // Update status message if it's showing a translatable key
        const statusEl = document.getElementById('status');
        if (statusEl && statusEl.hasAttribute('data-i18n')) {
            statusEl.textContent = t(statusEl.getAttribute('data-i18n'));
        }
    }

    async function init() {
        applyThemeColors();
        tg.ready();
        tg.expand();
        
        // Wait a bit for Telegram WebApp to populate initDataUnsafe
        // This ensures user data is available before rendering
        await new Promise(resolve => setTimeout(resolve, 100));

        renderUserInfo();
        
        // Re-check user info multiple times to ensure it gets loaded
        // This handles cases where Telegram WebApp initialization is slow
        let userDataFound = false;
        const checkUserInfo = (attemptNumber) => {
            // Check if user data is available
            if (tg.initDataUnsafe?.user) {
                if (!userDataFound) {
                    renderUserInfo();
                    userDataFound = true;
                }
                return true; // Stop checking once found
            }
            return false;
        };
        
        // Check at configured intervals with early exit
        const checkWithDelay = (delay, attemptNumber) => {
            setTimeout(() => {
                if (!userDataFound) {
                    checkUserInfo(attemptNumber);
                }
            }, delay);
        };
        
        checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[0], 1);
        checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[1], 2);
        checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[2], 3);
        
        setupEventListeners();
        
        // Apply initial translations
        applyTranslations();
        
        // Load initial data before showing welcome
        // Wrapped in try-catch to ensure showWelcome() is ALWAYS called
        try {
            await loadInitialData();
        } catch (err) {
            // Log any unexpected errors but continue to show welcome
            logger.error('init_load_data', 'Unexpected error during initial data load: ' + err.message, {
                stack: err.stack
            });
            state.initialDataLoadFailed = true;
        }
        
        // Re-render user info after loading data from backend
        // This ensures we show updated status if user ID was fetched from backend
        renderUserInfo();
        
        // Show welcome screen with loaded data (even if data loading failed)
        showWelcome();
    }

    async function loadInitialData() {
        // Fetch order contact info and user subscription status with timeout protection
        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error('Initial data load timeout')), CONFIG.INITIAL_DATA_TIMEOUT_MS);
        });
        
        const dataLoadPromise = (async () => {
            const headers = {};
            if (tg.initData) {
                headers['X-Telegram-Init-Data'] = tg.initData;
            }
            
            logger.apiCall('load_initial_data', 'started', { endpoint: '/api/products' });
            const startTime = Date.now();
            
            try {
                const response = await fetch('/api/products?limit=1', { headers });
                const responseTime = Date.now() - startTime;
                
                if (response.ok) {
                    const data = await response.json();
                    state.orderContact = data.order_contact || null;
                    state.botUsername = data.bot || null;
                    logger.apiCall('load_initial_data', 'success', { 
                        endpoint: '/api/products',
                        response_time_ms: responseTime,
                        order_contact: state.orderContact,
                        bot_username: state.botUsername
                    });
                } else {
                    logger.apiCall('load_initial_data', 'error', {
                        endpoint: '/api/products',
                        status: response.status,
                        response_time_ms: responseTime
                    });
                }
            } catch (err) {
                logger.error('load_initial_data_products', err.message, { stack: err.stack });
            }
            
            // Try to get user ID from multiple sources
            // 1. First try from tg.initDataUnsafe (frontend)
            if (tg.initDataUnsafe?.user?.id) {
                state.userId = tg.initDataUnsafe.user.id;
                if (!state.username && tg.initDataUnsafe.user.username) {
                    state.username = tg.initDataUnsafe.user.username;
                }
                logger.log('user_identification', 'user_id_from_frontend', 'success', '', {
                    user_id: state.userId,
                    source: 'tg.initDataUnsafe'
                });
            }
            
            // 2. If not available, try to get from backend via initData
            if (!state.userId && tg.initData) {
                try {
                    const userInfoResponse = await fetch('/api/user/info', { headers });
                    if (userInfoResponse.ok) {
                        const userInfo = await userInfoResponse.json();
                        if (userInfo.authenticated && userInfo.user_id) {
                            state.userId = userInfo.user_id;
                            logger.log('user_identification', 'user_id_from_backend', 'success', '', {
                                user_id: state.userId,
                                source: 'backend_initData',
                                username: userInfo.username
                            });
                            
                            // Also update state with other user info if not already set
                            if (!state.username && userInfo.username) {
                                state.username = userInfo.username;
                            }
                            if (userInfo.language_code && !state.userLanguage) {
                                state.userLanguage = userInfo.language_code;
                            }
                        }
                    }
                } catch (err) {
                    logger.error('load_user_info_from_backend', err.message, {
                        stack: err.stack
                    });
                }
            }
            
            // 3. Track user in bot_users database for proper ID logging on every deploy
            if (state.userId) {
                try {
                    const trackHeaders = { 'Content-Type': 'application/json' };
                    if (tg.initData) {
                        trackHeaders['X-Telegram-Init-Data'] = tg.initData;
                    }
                    const user = tg.initDataUnsafe?.user || {};
                    await fetch('/api/user/track', {
                        method: 'POST',
                        headers: trackHeaders,
                        body: JSON.stringify({
                            user_id: state.userId,
                            username: state.username || user.username || null,
                            first_name: user.first_name || null,
                            last_name: user.last_name || null
                        })
                    });
                    logger.log('user_tracking', 'user_tracked_in_db', 'success', '', {
                        user_id: state.userId
                    });
                } catch (err) {
                    logger.error('user_tracking', 'Failed to track user: ' + err.message);
                }
            }
            
            // Load user notification status if authenticated
            if (state.userId) {
                try {
                    await loadUserSubscriptionStatus();
                } catch (err) {
                    logger.error('load_subscription_status', 'Failed to load subscription: ' + err.message);
                }
            } else {
                logger.log('user_identification', 'user_id_unavailable', 'warning', '', {
                    message: 'Could not determine user ID from any source',
                    initData_present: !!tg.initData,
                    initDataUnsafe_present: !!tg.initDataUnsafe
                });
            }
        })();
        
        try {
            // Race between data loading and timeout
            await Promise.race([dataLoadPromise, timeoutPromise]);
        } catch (err) {
            // Handle timeout or other errors gracefully
            if (err.message === 'Initial data load timeout') {
                logger.error('load_initial_data', 'Timeout loading initial data', { 
                    timeout_ms: CONFIG.INITIAL_DATA_TIMEOUT_MS 
                });
                // Show warning to user but continue to show welcome screen
                state.initialDataLoadFailed = true;
            } else {
                logger.error('load_initial_data', err.message, { stack: err.stack });
                state.initialDataLoadFailed = true;
            }
        }
    }

    async function loadUserSubscriptionStatus() {
        if (!tg.initData || !state.userId) return;
        
        try {
            const headers = { 'X-Telegram-Init-Data': tg.initData };
            const response = await fetch(`/api/user/subscription?user_id=${state.userId}`, { headers });
            if (response.ok) {
                const data = await response.json();
                state.isSubscribed = data.is_subscribed !== false;
            }
        } catch (err) {
            logger.error('load_subscription_status', 'Failed to load subscription status: ' + err.message);
        }
    }

    function applyThemeColors() {
        if (tg.themeParams) {
            const params = tg.themeParams;
            document.documentElement.style.setProperty('--tg-theme-bg-color', params.bg_color || '#ffffff');
            document.documentElement.style.setProperty('--tg-theme-text-color', params.text_color || '#000000');
            document.documentElement.style.setProperty('--tg-theme-hint-color', params.hint_color || '#999999');
            document.documentElement.style.setProperty('--tg-theme-link-color', params.link_color || '#2481cc');
            document.documentElement.style.setProperty('--tg-theme-button-color', params.button_color || '#2481cc');
            document.documentElement.style.setProperty('--tg-theme-button-text-color', params.button_text_color || '#ffffff');
        }
    }

    function renderUserInfo() {
        const statusEl = document.getElementById('status');
        const userNameEl = document.getElementById('user-name');
        const langEl = document.getElementById('current-lang');

        // Check if we have user data from frontend or if user ID was fetched from backend
        if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
            const user = tg.initDataUnsafe.user;
            statusEl.textContent = t('status_connected');
            statusEl.classList.add('status-live');
            statusEl.classList.remove('status-preview');
            statusEl.classList.remove('status-loading');
            
            // Format username like bot does: @username or "First Last"
            let displayName;
            if (user.username) {
                displayName = `@${user.username}`;
                state.username = user.username;
            } else {
                displayName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || `User ${user.id}`;
                state.username = displayName;
            }
            userNameEl.textContent = displayName;
            state.userId = user.id;
            
            // Log user identification only if state changed (avoid duplicate logs)
            const userStateKey = `frontend_${user.id}`;
            if (state.lastLoggedUserStateId !== userStateKey) {
                logger.log('user_identification', 'user_info_loaded', 'success', '', {
                    user_id: user.id,
                    username: user.username || 'none',
                    first_name: user.first_name || '',
                    last_name: user.last_name || '',
                    language_code: user.language_code || 'none'
                });
                state.lastLoggedUserStateId = userStateKey;
            }
            
            // Get user language from Telegram
            if (user.language_code) {
                state.userLanguage = user.language_code;
            }
            const lang = findLanguageByCode(state.userLanguage);
            if (lang && langEl) {
                langEl.textContent = lang.name;
            }
        } else if (state.userId) {
            // User ID was fetched from backend but frontend data not available
            statusEl.textContent = t('status_connected');
            statusEl.classList.add('status-live');
            statusEl.classList.remove('status-preview');
            statusEl.classList.remove('status-loading');
            
            // Use stored username if available
            const displayName = state.username ? `@${state.username}` : `User ${state.userId}`;
            userNameEl.textContent = displayName;
            
            // Log user identification only if state changed (avoid duplicate logs)
            const userStateKey = `backend_${state.userId}`;
            if (state.lastLoggedUserStateId !== userStateKey) {
                logger.log('user_identification', 'user_info_from_backend', 'success', '', {
                    user_id: state.userId,
                    username: state.username || 'none',
                    source: 'backend'
                });
                state.lastLoggedUserStateId = userStateKey;
            }
            
            const lang = findLanguageByCode(state.userLanguage);
            if (lang && langEl) {
                langEl.textContent = lang.name;
            }
        } else {
            // Always set a proper status to avoid staying in "Loading..." indefinitely
            statusEl.textContent = t('status_preview');
            statusEl.classList.add('status-preview');
            statusEl.classList.remove('status-live');
            statusEl.classList.remove('status-loading');
            userNameEl.textContent = 'Guest';
            
            // Log preview mode only if state changed (avoid duplicate logs)
            const userStateKey = 'preview';
            if (state.lastLoggedUserStateId !== userStateKey) {
                logger.log('user_identification', 'preview_mode', 'warning', '', {
                    message: 'No Telegram user data available',
                    telegram_available: !!(window.Telegram && window.Telegram.WebApp),
                    initData_present: !!tg.initData,
                    initDataUnsafe_present: !!tg.initDataUnsafe,
                    user_present: !!(tg.initDataUnsafe && tg.initDataUnsafe.user)
                });
                state.lastLoggedUserStateId = userStateKey;
            }
        }
    }

    function setupEventListeners() {
        // Header buttons
        document.getElementById('home-btn').addEventListener('click', () => showWelcome());
        document.getElementById('search-btn').addEventListener('click', () => showSearch());
        document.getElementById('language-btn').addEventListener('click', () => showLanguageSelection());

        // Back button - use touchstart for instant response on mobile
        const backBtn = document.getElementById('back-btn');
        let backBtnTouchHandled = false;

        backBtn.addEventListener('touchstart', (e) => {
            backBtnTouchHandled = true;
            goBack();
            e.preventDefault(); // Prevent subsequent click event
        }, { passive: false });

        backBtn.addEventListener('click', () => {
            // Only handle click if touch wasn't used (for mouse/desktop)
            if (!backBtnTouchHandled) {
                goBack();
            }
            backBtnTouchHandled = false;
        });

        // Welcome screen actions
        document.querySelectorAll('.action-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const action = e.currentTarget.dataset.action;
                handleWelcomeAction(action);
            });
        });

        // Search
        const debouncedSearch = debounce(performSearch, CONFIG.SEARCH_DEBOUNCE_MS);
        document.getElementById('search-submit-btn').addEventListener('click', () => performSearch());
        document.getElementById('search-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performSearch();
            }
        });
        // Add input event for live search with debounce
        document.getElementById('search-input').addEventListener('input', debouncedSearch);

        // Add immediate touch feedback for all navigation buttons
        setupTouchFeedback();
    }

    /**
     * Add immediate visual feedback on touchstart for better responsiveness
     * This ensures buttons respond instantly on first touch on mobile devices
     */
    function setupTouchFeedback() {
        const buttonSelectors = [
            '.icon-btn',
            '.btn',
            '.action-btn',
            '.category-btn',
            '.language-btn',
            '.product-card',
            '#back-btn'
        ];

        buttonSelectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(element => {
                addTouchFeedback(element);
            });
        });
    }

    /**
     * Add touch feedback to a single element
     * @param {HTMLElement} element - The element to add touch feedback to
     */
    function addTouchFeedback(element) {
        // Avoid adding duplicate listeners
        if (element.dataset.touchFeedback === 'true') {
            return;
        }
        element.dataset.touchFeedback = 'true';

        // Add touchstart for immediate visual feedback
        element.addEventListener('touchstart', function() {
            this.style.opacity = '0.7';
        }, { passive: true });

        // Remove opacity change on touchend/touchcancel
        element.addEventListener('touchend', function() {
            this.style.opacity = '';
        }, { passive: true });

        element.addEventListener('touchcancel', function() {
            this.style.opacity = '';
        }, { passive: true });
    }

    function setStatus(message, isError = false) {
        const statusEl = document.getElementById('status');
        statusEl.textContent = message;
        statusEl.classList.toggle('error', Boolean(isError));
    }

    function pushHistory(viewData) {
        state.history.push({ ...viewData });
        updateBackButton();
    }

    function goBack() {
        logger.userInteraction('back_button_click', {
            current_view: state.view,
            history_length: state.history.length
        });
        
        if (state.history.length > 0) {
            const previous = state.history.pop();
            
            logger.navigationChange('go_back', {
                from_view: state.view,
                to_view: previous.view,
                category: previous.category,
                subcategory: previous.subcategory,
                page: previous.page
            });
            
            // Restore previous view
            if (previous.view === 'welcome') {
                showWelcome(false);
            } else if (previous.view === 'language') {
                showLanguageSelection(false);
            } else if (previous.view === 'categories') {
                // Check if we need to restore a subcategory view or main categories
                if (previous.category) {
                    // Restore subcategory view for the saved category
                    const category = state.categories.find(cat => cat.name === previous.category);
                    if (category) {
                        state.currentCategory = previous.category;
                        state.currentSubcategory = null;
                        logger.navigationChange('go_back_subcategory', {
                            category: previous.category,
                            subcategories_count: category.subcategories?.length || 0
                        });
                        showSubcategories(category, false);
                    } else {
                        // Category not found, fall back to main categories
                        logger.navigationChange('go_back_subcategory_fallback', {
                            category: previous.category,
                            reason: 'category_not_in_state'
                        });
                        showCategories(false);
                    }
                } else {
                    // Restore main categories view
                    showCategories(false);
                }
            } else if (previous.view === 'products') {
                loadProducts(previous.category, previous.subcategory, false, false);
            } else if (previous.view === 'search') {
                state.searchQuery = previous.query || '';
                state.page = previous.page || 0;
                showSearch(false);
                const searchInput = document.getElementById('search-input');
                if (searchInput) searchInput.value = state.searchQuery;
                if (state.searchResults && state.searchResults.length > 0) {
                    showSearchResults(state.searchResults, false);
                }
            }
        }
        updateBackButton();
    }

    function updateBackButton() {
        const backBtn = document.getElementById('back-btn');
        if (state.history.length > 0) {
            backBtn.style.display = 'block';
        } else {
            backBtn.style.display = 'none';
        }
    }

    function hideAllViews() {
        // Stop any playing media
        document.querySelectorAll('video, audio').forEach(media => {
            if (!media.paused) {
                media.pause();
            }
        });

        document.querySelectorAll('.view').forEach(view => {
            view.style.display = 'none';
        });
    }

    function handleWelcomeAction(action) {
        switch (action) {
            case 'catalog':
                loadCategories();
                break;
            case 'search':
                showSearch();
                break;
            case 'language':
                showLanguageSelection();
                break;
        }
    }

    function showWelcome(addToHistory = true) {
        if (addToHistory && state.view !== 'welcome') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }
        
        state.view = 'welcome';
        hideAllViews();
        document.getElementById('welcome-view').style.display = 'block';
        document.getElementById('breadcrumb').style.display = 'none';
        
        // Load order contact info if available
        if (state.orderContact) {
            const contactInfo = document.getElementById('order-contact-info');
            if (contactInfo) {
                const username = normalizeContactUsername(state.orderContact);
                contactInfo.innerHTML = `${t('order_contact_prefix')} <a href="https://t.me/${username}" target="_blank">${state.orderContact}</a>`;
            }
        }
        
        // Show warning if initial data load failed or timed out
        if (state.initialDataLoadFailed) {
            const contactInfo = document.getElementById('order-contact-info');
            if (contactInfo) {
                contactInfo.innerHTML = `⚠️ ${t('initial_data_timeout_warning')} <button id="retry-load-btn" style="margin-left: 10px; padding: 5px 10px; background-color: #ff9800; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em;">${t('btn_retry_load')}</button>`;
                contactInfo.style.color = '#ff9800';
                
                // Add click handler for retry button
                const retryBtn = document.getElementById('retry-load-btn');
                if (retryBtn) {
                    retryBtn.addEventListener('click', async () => {
                        retryBtn.disabled = true;
                        retryBtn.textContent = t('status_loading');
                        
                        // Reset the failed state and retry
                        state.initialDataLoadFailed = false;
                        
                        try {
                            await loadInitialData();
                            // If successful, refresh the welcome screen
                            renderUserInfo();
                            showWelcome(false);
                        } catch (err) {
                            logger.error('retry_initial_data', 'Retry failed: ' + err.message);
                            state.initialDataLoadFailed = true;
                            showWelcome(false);
                        }
                    });
                }
            }
        }

        setStatus(t('status_ready'));
    }

    function showLanguageSelection(addToHistory = true) {
        if (addToHistory && state.view !== 'language') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }
        
        state.view = 'language';
        hideAllViews();
        document.getElementById('language-view').style.display = 'block';
        document.getElementById('breadcrumb').style.display = 'none';
        
        const grid = document.getElementById('language-grid');
        grid.replaceChildren();
        
        state.languages.forEach(lang => {
            const btn = document.createElement('button');
            btn.className = 'language-btn';
            const isSelected = findLanguageByCode(state.userLanguage)?.code === lang.code;
            if (isSelected) {
                btn.classList.add('selected');
            }
            btn.innerHTML = `<span class="lang-flag">${lang.flag}</span><span class="lang-name">${lang.name}</span>${isSelected ? ' <span class="checkmark">✓</span>' : ''}`;
            btn.addEventListener('click', () => selectLanguage(lang));
            addTouchFeedback(btn);
            grid.appendChild(btn);
        });
        
        setStatus(t('language_title'));
    }

    function selectLanguage(lang) {
        state.userLanguage = lang.code;
        
        // Apply translations immediately
        applyTranslations();
        
        setStatus(t('language_changed', lang.name));
        
        // Close language selection view immediately
        goBack();
        
        // Reload data with new language in background
        setTimeout(async () => {
            try {
                // Reload categories with new language
                await loadCategories();
                
                // Reload current view content with new language if needed
                if (state.view === 'products' && (state.currentCategory || state.currentSubcategory)) {
                    // Reload products in current category/subcategory - don't add to history
                    await loadProducts(state.currentCategory, state.currentSubcategory, false, false);
                } else if (state.view === 'search' && state.searchQuery) {
                    // Re-run search with new language
                    await performSearch();
                }
            } catch (error) {
                logger.error('language_reload_failed', error.message, { stack: error.stack });
                setStatus(t('error_load_categories'), true);
            }
        }, 500);
    }

    function showSearch(addToHistory = true) {
        if (addToHistory && state.view !== 'search') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }

        logger.navigationChange('show_search', {
            from_view: state.view,
            category: state.currentCategory,
            subcategory: state.currentSubcategory
        });

        state.view = 'search';
        hideAllViews();
        document.getElementById('search-view').style.display = 'block';
        document.getElementById('breadcrumb').style.display = 'none';
        document.getElementById('search-input').focus();
        setStatus(t('status_enter_search'));
    }

    async function performSearch() {
        const query = document.getElementById('search-input').value.trim();
        
        if (!query || query.length < CONFIG.MIN_SEARCH_LENGTH) {
            document.getElementById('search-status').textContent = t('search_min_chars', CONFIG.MIN_SEARCH_LENGTH);
            return;
        }
        
        state.searchQuery = query;
        state.page = 0;
        
        document.getElementById('search-status').textContent = t('status_searching');
        
        logger.userInteraction('search', { query, query_length: query.length });

        try {
            const headers = {};
            if (tg.initData) {
                headers['X-Telegram-Init-Data'] = tg.initData;
            }
            
            const startTime = Date.now();
            
            // Use optimized backend search instead of fetching all products
            const encodedQuery = encodeURIComponent(query);
            const lang = state.userLanguage || 'en';
            const response = await fetchJsonWithDedup(
                `/api/search?q=${encodedQuery}&limit=100&offset=0&lang=${lang}`,
                { headers }
            );
            
            const responseTime = Date.now() - startTime;

            if (!response.ok) {
                const errorText = await response.text();
                logger.apiCall('search', 'error', { 
                    query, 
                    status: response.status, 
                    error: errorText,
                    response_time_ms: responseTime
                });
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            
            logger.apiCall('search', 'success', { 
                query, 
                results_count: data.items.length,
                total_matches: data.total,
                response_time_ms: responseTime
            });
            
            state.searchResults = data.items;
            state.totalProducts = data.total;
            
            showSearchResults(data.items, false);
            
        } catch (err) {
            logger.error('search_failed', err.message, { query, stack: err.stack });
            document.getElementById('search-status').textContent = t('search_failed');
        }
    }

    function showSearchResults(results, addToHistory = true) {
        if (addToHistory) {
            pushHistory({ view: 'search', query: state.searchQuery, page: state.page });
        }
        
        const grid = document.getElementById('search-results');
        const pagination = document.getElementById('search-pagination');
        const status = document.getElementById('search-status');
        
        grid.replaceChildren();
        pagination.replaceChildren();
        
        if (results.length === 0) {
            status.textContent = state.totalProducts > 0 
                ? `Found ${state.totalProducts} total results - showing page ${state.page + 1}` 
                : t('search_no_results');
            return;
        }
        
        // For server-side pagination, show all results from current page
        status.textContent = t('search_results_count', state.totalProducts, results.length);
        
        // Use DocumentFragment for batch DOM operations - improves rendering performance
        const fragment = document.createDocumentFragment();
        results.forEach(product => {
            const card = createProductCard(product);
            fragment.appendChild(card);
        });
        grid.appendChild(fragment);
        
        // Note: With backend search, we'd need to implement proper pagination
        // For now, limit results to first 100 from backend
    }

    async function loadCategories() {
        setStatus(t('status_loading_categories'));
        
        logger.userInteraction('load_categories', {});

        try {
            const headers = {};
            if (tg.initData) {
                headers['X-Telegram-Init-Data'] = tg.initData;
            }
            
            const startTime = Date.now();
            const lang = state.userLanguage || 'en';
            const response = await fetchJsonWithDedup(`/api/categories?lang=${lang}`, { headers });
            const responseTime = Date.now() - startTime;

            if (!response.ok) {
                const errorText = await response.text();
                logger.apiCall('load_categories', 'error', { 
                    status: response.status, 
                    error: errorText,
                    response_time_ms: responseTime
                });
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            state.categories = data.categories || [];
            state.allProductsCount = data.all_products_count || 0;
            
            logger.apiCall('load_categories', 'success', { 
                categories_count: state.categories.length,
                all_products_count: state.allProductsCount,
                response_time_ms: responseTime
            });
            
            showCategories();
        } catch (err) {
            logger.error('load_categories_failed', err.message, { stack: err.stack });
            setStatus(t('error_load_categories'), true);
            // Still show the categories view even if load fails, just with empty state
            showCategories();
        }
    }

    function showCategories(addToHistory = true) {
        if (addToHistory && state.view !== 'categories') {
            pushHistory({ view: state.view });
        }
        
        state.view = 'categories';
        state.currentCategory = null;
        state.currentSubcategory = null;
        state.page = 0;

        hideAllViews();
        document.getElementById('categories-view').style.display = 'block';
        
        document.getElementById('categories-title').textContent = t('categories_title');
        document.getElementById('categories-count').textContent = t('categories_count', state.categories.length);
        updateBreadcrumb();

        const grid = document.getElementById('categories-grid');
        grid.replaceChildren();

        // Use DocumentFragment for batch DOM operations - improves rendering performance
        const fragment = document.createDocumentFragment();

        // Add "All Products" button
        if (state.allProductsCount > 0) {
            const allBtn = createCategoryButton(
                'ALL',
                `📦 ${t('all_products')} (${state.allProductsCount})`,
                () => loadProducts(null, null)
            );
            fragment.appendChild(allBtn);
        }

        // Add category buttons
        state.categories.forEach(category => {
            if (category.count > 0) {  // Only show categories with products
                const btn = createCategoryButton(
                    category.name,
                    `${category.display_name} (${category.count})`,
                    () => handleCategoryClick(category)
                );
                fragment.appendChild(btn);
            }
        });
        
        grid.appendChild(fragment);

        setStatus(t('categories_available', state.categories.length));
    }

    function handleCategoryClick(category) {
        // Save current state before modifying it
        const previousCategory = state.currentCategory;
        state.currentCategory = category.name;

        if (category.has_subcategories && category.subcategories.length > 0) {
            // Show subcategories - pass previous category for history
            showSubcategories(category, true, previousCategory);
        } else {
            // Load products directly
            loadProducts(category.name, null);
        }
    }

    function showSubcategories(category, addToHistory = true, previousCategory = null) {
        if (addToHistory) {
            // Save the previous state (before we showed subcategories)
            pushHistory({ view: 'categories', category: previousCategory });
        }

        state.view = 'categories';  // Reuse categories view for subcategories
        state.currentSubcategory = null;  // Clear subcategory when entering subcategory selection view

        hideAllViews();
        document.getElementById('categories-view').style.display = 'block';

        logger.navigationChange('show_subcategories', {
            category: category.name,
            subcategories_count: category.subcategories?.length || 0
        });

        document.getElementById('categories-title').textContent = category.display_name;
        document.getElementById('categories-count').textContent = t('subcategories_count', category.subcategories.length);
        updateBreadcrumb();

        const grid = document.getElementById('categories-grid');
        grid.replaceChildren();

        // Use DocumentFragment for batch DOM operations - improves rendering performance
        const fragment = document.createDocumentFragment();

        // Add "All in Category" button
        const allBtn = createCategoryButton(
            'ALL',
            t('all_in_category', category.display_name) + ` (${category.count})`,
            () => loadProducts(category.name, null)
        );
        fragment.appendChild(allBtn);

        // Add subcategory buttons
        category.subcategories.forEach(subcat => {
            if (subcat.count > 0) {
                const btn = createCategoryButton(
                    subcat.name,
                    `${subcat.display_name} (${subcat.count})`,
                    () => loadProducts(category.name, subcat.name)
                );
                fragment.appendChild(btn);
            }
        });
        
        grid.appendChild(fragment);

        setStatus(t('subcategories_in', category.display_name));
    }

    function createCategoryButton(key, text, onClick) {
        const btn = document.createElement('button');
        btn.className = 'category-btn';
        btn.textContent = text;
        btn.addEventListener('click', onClick);
        addTouchFeedback(btn);
        return btn;
    }

    /**
     * Load products from the API with optional filtering by category/subcategory.
     * @param {string|null} category - Category to filter by, or null for all products
     * @param {string|null} subcategory - Subcategory to filter by, or null for all in category
     * @param {boolean} resetPage - If true, reset to page 0; if false, keep current page (for pagination)
     * @param {boolean} addToHistory - If true, add current state to history; if false, skip history (for pagination/reload)
     */
    async function loadProducts(category, subcategory, resetPage = true, addToHistory = true) {
        // Prevent duplicate loads
        if (state.loadingInProgress) {
            logger.log('api_call', 'load_products_skipped', 'info', '', { reason: 'already_loading' });
            return;
        }

        // Only add to history when navigating to products view, not when paginating
        if (addToHistory) {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory });
        }

        state.currentCategory = category;
        state.currentSubcategory = subcategory;
        // Only reset page when loading a new category/subcategory, not when paginating
        if (resetPage) {
            state.page = 0;
        }
        state.view = 'products';
        
        hideAllViews();
        document.getElementById('products-view').style.display = 'block';
        updateBreadcrumb();
        
        document.getElementById('products-status').textContent = t('status_loading_products');
        
        logger.userInteraction('load_products', { category, subcategory, page: state.page });

        try {
            state.loadingInProgress = true;
            
            const headers = {};
            if (tg.initData) {
                headers['X-Telegram-Init-Data'] = tg.initData;
            }
            
            let url = `/api/products?limit=${state.itemsPerPage}&offset=${state.page * state.itemsPerPage}`;
            if (category) url += `&category=${encodeURIComponent(category)}`;
            if (subcategory) url += `&subcategory=${encodeURIComponent(subcategory)}`;
            const lang = state.userLanguage || 'en';
            url += `&lang=${lang}`;

            // Check cache first
            const cacheKey = url;
            const cached = state.cache[cacheKey];
            const cacheAge = cached ? Date.now() - cached.timestamp : Infinity;
            
            // Use cache if less than 30 seconds old
            if (cached && cacheAge < 30000) {
                logger.apiCall('load_products', 'cache_hit', { 
                    url, 
                    cache_age_ms: cacheAge,
                    items_count: cached.data.items?.length || 0
                });
                state.products = cached.data.items || [];
                state.totalProducts = cached.data.total || 0;
                state.orderContact = cached.data.order_contact || null;
                state.botUsername = cached.data.bot || null;
                showProducts(false);
                state.loadingInProgress = false;
                return;
            }

            const startTime = Date.now();
            const response = await fetchJsonWithDedup(url, { headers });
            const responseTime = Date.now() - startTime;

            if (!response.ok) {
                const errorText = await response.text();
                logger.apiCall('load_products', 'error', { 
                    url, 
                    status: response.status, 
                    error: errorText,
                    response_time_ms: responseTime
                });
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            
            logger.apiCall('load_products', 'success', { 
                url,
                items_count: data.items?.length || 0,
                total: data.total,
                response_time_ms: responseTime
            });
            
            // Cache the response
            state.cache[cacheKey] = {
                data: data,
                timestamp: Date.now()
            };
            
            // Prevent memory leaks: cleanup old cache entries (LRU-like policy)
            const cacheKeys = Object.keys(state.cache);
            if (cacheKeys.length > CONFIG.MAX_CACHE_SIZE) {
                // Remove oldest entry
                const oldestKey = cacheKeys.reduce((oldest, key) => {
                    return state.cache[key].timestamp < state.cache[oldest].timestamp ? key : oldest;
                }, cacheKeys[0]);
                delete state.cache[oldestKey];
            }
            
            state.products = data.items || [];
            state.totalProducts = data.total || 0;
            state.orderContact = data.order_contact || null;
            state.botUsername = data.bot || null;

            showProducts(false);
        } catch (err) {
            logger.error('load_products', err.message);
            document.getElementById('products-status').textContent = t('error_load_products');
        } finally {
            state.loadingInProgress = false;
        }
    }

    function showProducts(addToHistory = true) {
        // Update title
        let title = t('all_products');
        if (state.currentCategory) {
            const categoryData = state.categories.find(c => c.name === state.currentCategory);
            const categoryName = categoryData ? categoryData.display_name : state.currentCategory;
            
            if (state.currentSubcategory) {
                title = state.currentSubcategory;
            } else {
                title = categoryName;
            }
        }

        document.getElementById('products-title').textContent = title;
        document.getElementById('products-count').textContent = t('items_count', state.totalProducts);

        const grid = document.getElementById('products-grid');
        const pagination = document.getElementById('products-pagination');
        
        grid.replaceChildren();
        pagination.replaceChildren();

        if (!state.products.length) {
            document.getElementById('products-status').textContent = t('no_products');
            return;
        }

        const totalPages = Math.ceil(state.totalProducts / state.itemsPerPage);
        const start = state.page * state.itemsPerPage;
        const end = Math.min(start + state.itemsPerPage, state.totalProducts);

        document.getElementById('products-status').textContent = t('showing_range', start + 1, end, state.totalProducts);

        // Use DocumentFragment for batch DOM operations - improves rendering performance
        const fragment = document.createDocumentFragment();
        state.products.forEach(product => {
            const card = createProductCard(product);
            fragment.appendChild(card);
        });
        grid.appendChild(fragment);

        // Pagination
        if (totalPages > 1) {
            renderPagination(pagination, state.page, totalPages, (newPage) => {
                debouncedPageChange(newPage, state.currentCategory, state.currentSubcategory);
            });
        }
    }

    function renderPagination(container, currentPage, totalPages, onPageChange) {
        container.replaceChildren();
        
        const prevBtn = document.createElement('button');
        prevBtn.className = 'btn btn-secondary';
        prevBtn.textContent = t('btn_prev');
        prevBtn.disabled = currentPage === 0;
        prevBtn.addEventListener('click', () => onPageChange(currentPage - 1));
        addTouchFeedback(prevBtn);
        container.appendChild(prevBtn);

        const pageInfo = document.createElement('span');
        pageInfo.className = 'page-info';
        pageInfo.textContent = t('page_info', currentPage + 1, totalPages);
        container.appendChild(pageInfo);

        const nextBtn = document.createElement('button');
        nextBtn.className = 'btn btn-secondary';
        nextBtn.textContent = t('btn_next');
        nextBtn.disabled = currentPage >= totalPages - 1;
        nextBtn.addEventListener('click', () => onPageChange(currentPage + 1));
        addTouchFeedback(nextBtn);
        container.appendChild(nextBtn);
    }

    function createProductCard(item) {
        // NOTE: All <img> elements created here must include an alt attribute for SEO and accessibility.
        const card = document.createElement('div');
        card.className = 'product-card';
        card.addEventListener('click', () => showProductDetail(item));
        addTouchFeedback(card);

        const media = document.createElement('div');
        media.className = 'card-media';

        // For products with multiple media, prefer showing a photo over video
        let mediaIndex = 0;
        if (item.media_count > 1 && item.media_info && Array.isArray(item.media_info)) {
            // Find first photo in media_info array
            const photoIndex = item.media_info.findIndex(m => m.type === 'photo');
            if (photoIndex !== -1) {
                mediaIndex = photoIndex;
            }
        }

        const mediaUrl = buildMediaUrl(item, mediaIndex);
        if (mediaUrl) {
            const img = document.createElement('img');
            img.src = mediaUrl;
            img.alt = item.caption || 'Product';
            img.loading = 'lazy';
            img.onload = () => {
                img.classList.add('loaded');
            };
            img.onerror = () => {
                media.classList.add('placeholder');
                media.textContent = '🖼️';
            };
            media.appendChild(img);
        } else {
            media.classList.add('placeholder');
            media.textContent = '🖼️';
        }

        if (item.media_count > 1) {
            const badge = document.createElement('div');
            badge.className = 'media-count-badge';
            badge.textContent = `${item.media_count} 📷`;
            media.appendChild(badge);
        }

        const body = document.createElement('div');
        body.className = 'card-body';

        const title = document.createElement('h3');
        const captionText = item.caption || 'Untitled';
        title.textContent = captionText.length > CONFIG.MAX_CARD_CAPTION_LENGTH 
            ? captionText.substring(0, CONFIG.MAX_CARD_CAPTION_LENGTH) + '...' 
            : captionText;

        const meta = document.createElement('p');
        meta.className = 'card-meta';
        const category = item.category || 'Uncategorized';
        const subcategory = item.subcategory_display ? ` • ${item.subcategory_display}` : '';
        meta.textContent = `${category}${subcategory}`;

        const footer = document.createElement('div');
        footer.className = 'card-footer';
        footer.textContent = formatDate(item.created_at) || '';

        body.appendChild(title);
        body.appendChild(meta);
        body.appendChild(footer);

        card.appendChild(media);
        card.appendChild(body);

        return card;
    }

    function buildMediaUrl(item, mediaIndex) {
        if (!item.media_count || !item.id) return null;
        if (tg.initData) {
            const encodedInit = encodeURIComponent(tg.initData);
            return `/api/catalog/${item.id}/media/${mediaIndex}?init_data=${encodedInit}`;
        } else {
            // Preview mode - no initData
            return `/api/catalog/${item.id}/media/${mediaIndex}`;
        }
    }

    function formatDate(dateString) {
        if (!dateString) return '';
        const d = new Date(dateString);
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function showProductDetail(product) {
        logger.userInteraction('view_product', { 
            product_id: product.id,
            category: product.category,
            subcategory: product.subcategory
        });
        
        // Save current state to history for back button
        pushHistory({
            view: state.view,
            category: state.currentCategory,
            subcategory: state.currentSubcategory,
            page: state.page
        });
        
        // Update state
        state.view = 'product-detail';
        state.currentProduct = product;
        
        // Hide all other views
        hideAllViews();
        
        // Show product detail view
        const detailView = document.getElementById('product-detail-view');
        const detailContent = document.getElementById('product-detail-content');
        
        detailContent.replaceChildren();

        // Product title
        const title = document.createElement('h2');
        title.textContent = product.caption || 'Untitled Product';
        title.style.marginBottom = '12px';
        title.style.wordBreak = 'break-word';
        title.style.whiteSpace = 'pre-line';
        detailContent.appendChild(title);

        // Category info
        const category = document.createElement('p');
        category.className = 'card-meta';
        category.style.marginBottom = '16px';
        const categoryText = product.category || 'Uncategorized';
        const subcategoryText = product.subcategory_display ? ` • ${product.subcategory_display}` : '';
        category.textContent = `${categoryText}${subcategoryText}`;
        detailContent.appendChild(category);

        const mediaCount = product.media_count || 1;
        const mediaInfo = product.media_info || [{ type: product.file_type || 'photo' }];
        
        // Create media display container
        const mediaContainer = document.createElement('div');
        mediaContainer.style.marginBottom = '20px';
        
        // Create main media viewer (for expanded view)
        const mediaViewer = document.createElement('div');
        mediaViewer.className = 'media-viewer';
        mediaViewer.style.position = 'relative';
        mediaViewer.style.width = '100%';
        mediaViewer.style.minHeight = '300px';
        mediaViewer.style.maxHeight = '500px';
        mediaViewer.style.display = 'flex';
        mediaViewer.style.alignItems = 'center';
        mediaViewer.style.justifyContent = 'center';
        mediaViewer.style.backgroundColor = '#000';
        mediaViewer.style.borderRadius = '12px';
        mediaViewer.style.overflow = 'hidden';
        mediaViewer.style.marginBottom = '16px';
        
        // Track current media index
        let currentMediaIndex = 0;
        
        // Function to render media at index in main viewer
        const renderMedia = (index) => {
            mediaViewer.replaceChildren();
            const mediaUrl = buildMediaUrl(product, index);
            const mediaType = mediaInfo[index]?.type || 'photo';
            
            logger.userInteraction('view_media', {
                product_id: product.id,
                media_index: index,
                media_type: mediaType
            });
            
            if (mediaUrl) {
                if (mediaType === 'video') {
                    logger.videoEvent('video_load_attempt', 'started', '', {
                        product_id: product.id,
                        media_index: index,
                        url: mediaUrl
                    });
                    
                    // Show loading indicator
                    const loadingDiv = document.createElement('div');
                    loadingDiv.className = 'media-loading';
                    loadingDiv.innerHTML = `
                        <div class="media-loading-icon">⏳</div>
                        <div class="media-loading-text">Loading video...</div>
                    `;
                    mediaViewer.appendChild(loadingDiv);
                    
                    const video = document.createElement('video');
                    video.controls = true;
                    video.playsInline = true;
                    video.setAttribute('playsinline', ''); // Force inline playback
                    video.muted = true; // Must be set before src for mobile autoplay
                    video.autoplay = true; // Attribute needed for mobile browser autoplay
                    // Use auto preload for faster buffering and reduced load time
                    video.preload = 'auto';
                    video.style.maxWidth = '100%';
                    video.style.maxHeight = '500px';
                    video.style.width = '100%';
                    video.style.height = 'auto';
                    video.style.display = 'none'; // Hide until loaded
                    video.style.zIndex = '10';             // Ensure it sits on top
                    video.style.position = 'relative';     // Ensure z-index applies
                    
                    // Use MediaRequestManager for intelligent request handling
                    mediaRequestManager.request(mediaUrl, product.id, index)
                        .then(blobUrl => {
                            // Set video source from blob URL (deduplicated and cached)
                            video.src = blobUrl;
                        })
                        .catch(error => {
                            // Handle request error
                            logger.videoEvent('video_load_error', 'error', error.message, {
                                product_id: product.id,
                                media_index: index
                            });
                            
                            mediaViewer.innerHTML = `
                                <div class="media-error">
                                    <div class="media-error-icon">⚠️</div>
                                    <div class="media-error-title">Video Unavailable</div>
                                    <div class="media-error-details">${error.message}</div>
                                    <div class="media-error-hint">
                                        👇 Try the "Get Product in Bot Chat" button below
                                    </div>
                                </div>
                            `;
                        });
                    
                    // Track if video has been shown to avoid duplicate handling
                    let videoShown = false;
                    const showVideo = () => {
                        if (videoShown) return;
                        videoShown = true;
                        loadingDiv.style.display = 'none';
                        video.style.display = 'block';
                    };
                    
                    // Show video as soon as we have any data to play
                    video.addEventListener('canplay', () => {
                        logger.videoEvent('video_load_success', 'success', '', {
                            product_id: product.id,
                            media_index: index,
                            duration: video.duration
                        });
                        
                        showVideo();
                        
                        // Auto-play video when loaded (muted already set at creation)
                        video.play().catch(err => {
                            logger.videoEvent('video_autoplay_failed', 'warning', err.message, {
                                product_id: product.id,
                                media_index: index
                            });
                        });
                    });
                    
                    // Also show on loadeddata as fallback
                    video.addEventListener('loadeddata', () => {
                        showVideo();
                    });
                    
                    // Timeout: if video hasn't loaded after configured time, show play button overlay
                    const videoLoadTimeout = setTimeout(() => {
                        if (!videoShown && video.readyState < 3) {
                            loadingDiv.innerHTML = `
                                <div class="media-loading-icon">▶️</div>
                                <div class="media-loading-text">Tap to play video</div>
                            `;
                            loadingDiv.style.cursor = 'pointer';
                            loadingDiv.addEventListener('click', () => {
                                showVideo();
                                video.muted = true;
                                video.play().catch(() => {});
                            }, { once: true });
                        }
                    }, CONFIG.VIDEO_LOAD_TIMEOUT_MS);
                    
                    video.addEventListener('error', async (e) => {
                        clearTimeout(videoLoadTimeout);
                        // Check if this is a large file error by fetching the URL
                        try {
                            const response = await fetch(mediaUrl);
                            if (response.status === 413 && response.headers.get('content-type')?.includes('application/json')) {
                                const errorData = await response.json();
                                mediaViewer.innerHTML = `
                                    <div class="media-error">
                                        <div class="media-error-icon">📦</div>
                                        <div class="media-error-title">File Too Large for Web Playback</div>
                                        <div class="media-error-message">Size: ${(errorData.file_size / (1024 * 1024)).toFixed(1)} MB</div>
                                        <div class="media-error-hint">
                                            ${errorData.message || "This video is too large to stream in the browser."}
                                        </div>
                                        <div class="media-error-hint">
                                            👇 Use the button below to get it in the bot chat
                                        </div>
                                    </div>
                                `;
                                return;
                            }
                        } catch (fetchErr) {
                            // Fall through to default error handling
                        }
                        
                        const errorMsg = video.error ? `Error code: ${video.error.code}, ${video.error.message}` : 'Unknown error';
                        logger.videoEvent('video_load_error', 'error', errorMsg, {
                            product_id: product.id,
                            media_index: index,
                            error_code: video.error?.code,
                            error_message: video.error?.message,
                            network_state: video.networkState,
                            ready_state: video.readyState
                        });
                        
                        mediaViewer.innerHTML = `
                            <div class="media-error">
                                <div class="media-error-icon">⚠️</div>
                                <div class="media-error-title">Video Unavailable</div>
                                <div class="media-error-details">${errorMsg}</div>
                                <div class="media-error-hint">
                                    👇 Try the "Get Product in Bot Chat" button below
                                </div>
                            </div>
                        `;
                    });
                    
                    video.addEventListener('play', () => {
                        logger.videoEvent('video_play', 'success', '', {
                            product_id: product.id,
                            media_index: index
                        });
                    });
                    
                    mediaViewer.appendChild(video);
                } else {
                    const img = document.createElement('img');
                    img.src = mediaUrl;
                    img.alt = `${product.caption || 'Product'} - Image ${index + 1}`;
                    img.style.maxWidth = '100%';
                    img.style.maxHeight = '500px';
                    img.style.width = 'auto';
                    img.style.height = 'auto';
                    img.style.objectFit = 'contain';
                    // Use eager loading for the actively displayed main viewer image;
                    // lazy loading would defer loading and cause a blank frame on navigation.
                    img.loading = 'eager';
                    img.onload = () => {
                        img.classList.add('loaded');
                    };
                    img.onerror = () => {
                        logger.error('image_load_error', 'Failed to load image', {
                            product_id: product.id,
                            media_index: index,
                            url: mediaUrl
                        });
                        mediaViewer.innerHTML = `
                            <div class="media-error">
                                <div class="media-error-icon">❌</div>
                                <div class="media-error-title">Image Unavailable</div>
                                <div class="media-error-hint">Try the "Get Product in Bot Chat" button below</div>
                            </div>
                        `;
                    };
                    mediaViewer.appendChild(img);
                }
            } else {
                mediaViewer.innerHTML = `
                    <div class="media-error">
                        <div class="media-error-icon">📷</div>
                        <div class="media-error-title">No Media</div>
                    </div>
                `;
            }
            
            // Update counter and thumbnail highlights
            if (mediaCount > 1 && counter) {
                counter.textContent = `${index + 1} / ${mediaCount}`;
            }
            
            // Update thumbnail highlights using CSS classes
            document.querySelectorAll('.media-thumbnail').forEach((thumb, i) => {
                if (i === index) {
                    thumb.classList.add('active');
                } else {
                    thumb.classList.remove('active');
                }
            });
            
            // Update button states
            if (prevBtn) prevBtn.disabled = index === 0;
            if (nextBtn) nextBtn.disabled = index >= mediaCount - 1;
        };
        
        mediaContainer.appendChild(mediaViewer);
        
        // Create thumbnail grid if multiple media
        if (mediaCount > 1) {
            const thumbnailGrid = document.createElement('div');
            thumbnailGrid.className = 'thumbnail-grid';
            
            // Create thumbnails for all media
            for (let i = 0; i < mediaCount; i++) {
                const thumbContainer = document.createElement('div');
                thumbContainer.className = i === 0 ? 'media-thumbnail active' : 'media-thumbnail';
                
                const thumbContent = document.createElement('div');
                thumbContent.className = 'thumb-content';
                
                const mediaType = mediaInfo[i]?.type || 'photo';
                const thumbUrl = buildMediaUrl(product, i);
                
                if (mediaType === 'video') {
                    // Video thumbnail with preview image and play icon overlay
                    if (thumbUrl) {
                        const thumbVideo = document.createElement('video');
                        thumbVideo.src = thumbUrl;
                        thumbVideo.preload = 'metadata';
                        thumbVideo.style.width = '100%';
                        thumbVideo.style.height = '100%';
                        thumbVideo.style.objectFit = 'cover';
                        thumbVideo.style.position = 'absolute';
                        thumbVideo.style.top = '0';
                        thumbVideo.style.left = '0';
                        
                        // Set currentTime after metadata is loaded for reliable thumbnail
                        thumbVideo.addEventListener('loadedmetadata', () => {
                            // For very short videos, use a minimal safe seek
                            if (thumbVideo.duration < 0.5) {
                                thumbVideo.currentTime = Math.max(0, thumbVideo.duration * CONFIG.VIDEO_THUMBNAIL_SEEK_PERCENT);
                                return;
                            }
                            // Use 10% of duration for short videos, cap at 1 second for long videos
                            // This ensures good frames: short videos use proportional seek, long videos don't seek too far
                            const seekTime = Math.min(CONFIG.VIDEO_THUMBNAIL_SEEK_TIME, thumbVideo.duration * CONFIG.VIDEO_THUMBNAIL_SEEK_PERCENT);
                            // Ensure non-negative time and stay within video bounds
                            thumbVideo.currentTime = Math.max(0, Math.min(seekTime, thumbVideo.duration - CONFIG.VIDEO_BOUNDS_BUFFER));
                        });
                        
                        thumbVideo.onerror = () => {
                            // Fallback to icon if video fails to load
                            thumbContent.innerHTML = '<div style="color: white; font-size: 24px;">▶️</div>';
                        };
                        
                        thumbContent.appendChild(thumbVideo);
                    }
                    
                    // Add play icon overlay
                    const playIcon = document.createElement('div');
                    playIcon.style.position = 'absolute';
                    playIcon.style.top = '50%';
                    playIcon.style.left = '50%';
                    playIcon.style.transform = 'translate(-50%, -50%)';
                    playIcon.style.fontSize = '32px';
                    playIcon.style.color = 'white';
                    playIcon.style.textShadow = '0 2px 4px rgba(0,0,0,0.8)';
                    playIcon.textContent = '▶️';
                    thumbContent.appendChild(playIcon);
                } else {
                    // Photo thumbnail with actual image preview
                    if (thumbUrl) {
                        const thumbImg = document.createElement('img');
                        thumbImg.src = thumbUrl;
                        thumbImg.alt = product.caption || 'Product media';
                        thumbImg.style.width = '100%';
                        thumbImg.style.height = '100%';
                        thumbImg.style.objectFit = 'cover';
                        thumbImg.style.position = 'absolute';
                        thumbImg.style.top = '0';
                        thumbImg.style.left = '0';
                        // Use eager loading for thumbnails since they're visible immediately
                        thumbImg.loading = 'eager';
                        thumbImg.onload = () => {
                            // Ensure image is visible when loaded
                            thumbImg.style.opacity = '1';
                        };
                        thumbImg.onerror = () => {
                            // Fallback to icon if image fails to load
                            thumbContent.innerHTML = '<div style="color: white; font-size: 24px;">📷</div>';
                        };
                        thumbContent.appendChild(thumbImg);
                    } else {
                        // Fallback to icon if no URL available
                        thumbContent.innerHTML = '<div style="color: white; font-size: 24px;">📷</div>';
                    }
                }
                
                thumbContainer.appendChild(thumbContent);
                
                // Add hover effect
                thumbContainer.addEventListener('mouseenter', () => {
                    if (currentMediaIndex !== i) {
                        thumbContainer.style.transform = 'scale(1.05)';
                    }
                });
                
                thumbContainer.addEventListener('mouseleave', () => {
                    if (currentMediaIndex !== i) {
                        thumbContainer.style.transform = 'scale(1)';
                    }
                });
                
                // Click to expand
                thumbContainer.addEventListener('click', () => {
                    currentMediaIndex = i;
                    renderMedia(i);
                    logger.userInteraction('thumbnail_click', { 
                        product_id: product.id, 
                        media_index: i 
                    });
                });
                
                thumbnailGrid.appendChild(thumbContainer);
            }
            
            mediaContainer.appendChild(thumbnailGrid);
        }
        
        // Add navigation controls if multiple media
        let prevBtn, nextBtn, counter;
        if (mediaCount > 1) {
            const controls = document.createElement('div');
            controls.style.display = 'flex';
            controls.style.justifyContent = 'space-between';
            controls.style.alignItems = 'center';
            controls.style.marginBottom = '16px';
            controls.style.gap = '12px';
            
            prevBtn = document.createElement('button');
            prevBtn.className = 'btn btn-secondary nav-button';
            prevBtn.textContent = t('btn_prev');
            prevBtn.style.flex = '1';
            prevBtn.style.minHeight = '56px';
            prevBtn.style.fontSize = '16px';
            prevBtn.style.fontWeight = '600';
            prevBtn.style.border = '2px solid rgba(0, 0, 0, 0.1)';
            prevBtn.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.1)';
            prevBtn.disabled = true;
            prevBtn.addEventListener('click', () => {
                if (currentMediaIndex > 0) {
                    currentMediaIndex--;
                    renderMedia(currentMediaIndex);
                    logger.userInteraction('carousel_prev', { 
                        product_id: product.id, 
                        new_index: currentMediaIndex 
                    });
                }
            });
            
            counter = document.createElement('span');
            counter.style.fontWeight = 'bold';
            counter.style.fontSize = '16px';
            counter.style.minWidth = '60px';
            counter.style.textAlign = 'center';
            counter.textContent = `1 / ${mediaCount}`;
            
            nextBtn = document.createElement('button');
            nextBtn.className = 'btn btn-secondary nav-button';
            nextBtn.textContent = t('btn_next');
            nextBtn.style.flex = '1';
            nextBtn.style.minHeight = '56px';
            nextBtn.style.fontSize = '16px';
            nextBtn.style.fontWeight = '600';
            nextBtn.style.border = '2px solid rgba(0, 0, 0, 0.1)';
            nextBtn.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.1)';
            nextBtn.disabled = mediaCount <= 1;
            nextBtn.addEventListener('click', () => {
                if (currentMediaIndex < mediaCount - 1) {
                    currentMediaIndex++;
                    renderMedia(currentMediaIndex);
                    logger.userInteraction('carousel_next', { 
                        product_id: product.id, 
                        new_index: currentMediaIndex 
                    });
                }
            });
            
            controls.appendChild(prevBtn);
            controls.appendChild(counter);
            controls.appendChild(nextBtn);
            mediaContainer.appendChild(controls);
        }
        
        // Render initial media
        renderMedia(0);
        
        detailContent.appendChild(mediaContainer);

        // Action buttons container
        const actionsContainer = document.createElement('div');
        actionsContainer.style.display = 'flex';
        actionsContainer.style.flexDirection = 'column';
        actionsContainer.style.gap = '12px';
        actionsContainer.style.marginTop = '20px';

        // DM to order button
        if (state.orderContact) {
            const dmUrl = getOrderUrl();
            if (dmUrl) {
                const dmBtn = document.createElement('a');
                dmBtn.href = dmUrl;
                dmBtn.target = '_blank';
                dmBtn.rel = 'noopener';
                dmBtn.className = 'btn btn-primary';
                dmBtn.style.minHeight = '52px';
                dmBtn.style.fontSize = '16px';
                dmBtn.style.lineHeight = '52px';
                dmBtn.style.padding = '0 24px';
                dmBtn.textContent = t('btn_dm_to_order');
                dmBtn.addEventListener('click', () => {
                    logger.userInteraction('dm_to_order_click', {
                        product_id: product.id,
                        order_contact: state.orderContact
                    });
                });
                actionsContainer.appendChild(dmBtn);
            }
        }

        detailContent.appendChild(actionsContainer);

        // Show the view
        detailView.style.display = 'block';
        
        // Show back button
        document.getElementById('back-btn').style.display = 'block';
        
        // Update breadcrumb
        updateBreadcrumb();
        
        logger.navigationChange('show_product_detail', {
            product_id: product.id,
            from_view: state.history[state.history.length - 1]?.view
        });
    }

    function getOrderUrl() {
        if (!state.orderContact) return null;
        const username = normalizeContactUsername(state.orderContact);
        return username ? `https://t.me/${username}` : null;
    }

    function updateBreadcrumb() {
        const breadcrumbEl = document.getElementById('breadcrumb');
        const parts = [];

        parts.push(`<a href="#" class="breadcrumb-link" data-action="home">${t('home')}</a>`);

        if (state.currentCategory) {
            const categoryData = state.categories.find(c => c.name === state.currentCategory);
            const categoryName = categoryData ? categoryData.display_name : state.currentCategory;
            
            if (state.currentSubcategory) {
                parts.push(`<a href="#" class="breadcrumb-link" data-action="category">${categoryName}</a>`);
                parts.push(`<span>${state.currentSubcategory}</span>`);
            } else if (state.view === 'products') {
                parts.push(`<span>${categoryName}</span>`);
            } else {
                parts.push(`<span>${categoryName}</span>`);
            }
        }

        if (parts.length > 1) {
            breadcrumbEl.innerHTML = parts.join(' › ');
            breadcrumbEl.style.display = 'block';
            
            breadcrumbEl.querySelectorAll('.breadcrumb-link').forEach(link => {
                link.addEventListener('click', (e) => {
                    e.preventDefault();
                    const action = e.target.dataset.action;
                    if (action === 'home') {
                        // Clear history and go home
                        state.history = [];
                        showCategories();
                    } else if (action === 'category') {
                        // Go back to category view
                        goBack();
                    }
                });
            });
        } else {
            breadcrumbEl.style.display = 'none';
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
