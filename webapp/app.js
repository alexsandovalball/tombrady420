// Tom Brady Menu - Full Bot-like WebApp Interface
(function () {
    'use strict';

    // Configuration constants
    const CONFIG = {
        MIN_SEARCH_LENGTH: 2,
        MAX_CARD_CAPTION_LENGTH: 47,
        ITEMS_PER_PAGE: 6,
        PRODUCT_CACHE_TTL_MS: 120000,
        LOG_BATCH_SIZE: 10,
        LOG_BATCH_INTERVAL: 30000,  // 30 seconds
        VIDEO_THUMBNAIL_SEEK_TIME: 1,  // 1 second
        VIDEO_THUMBNAIL_SEEK_PERCENT: 0.1,  // 10% of duration
        VIDEO_BOUNDS_BUFFER: 0.1,  // Buffer to stay within video bounds
        VIDEO_SHORT_DURATION_THRESHOLD: 0.5,  // Videos shorter than this use proportional seek
        VIDEO_LOAD_TIMEOUT_MS: 10000,  // Timeout before showing tap-to-play for slow videos
        USER_DATA_CHECK_INTERVALS: [300, 700, 1500],  // Retry intervals in ms for user data loading
        SEARCH_DEBOUNCE_MS: 300,  // Debounce time for search input
        LANGUAGE_STORAGE_KEY: 'bradycadia_webapp_language',
        WEBAPP_CLOSE_DELAY_MS: 1500,  // Delay before closing webapp after successful product send
        ERROR_DISPLAY_MS: 4000,  // Duration to show error messages on buttons
        INITIAL_DATA_TIMEOUT_MS: 10000,  // Timeout for initial data load to prevent infinite loading
        MAX_VIDEO_PREVIEW_CACHE_SIZE: 24,  // Captured video preview frames kept in memory
        SITE_ICON_URL: '/assets/site-icon.svg?v=20260805-football-emoji-canonical-v2',
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

    window.addEventListener('error', (event) => {
        logger.error('window_error', event.message || 'Unhandled window error', {
            filename: event.filename || '',
            lineno: event.lineno || 0,
            colno: event.colno || 0
        });
    });

    window.addEventListener('unhandledrejection', (event) => {
        const reason = event.reason;
        logger.error(
            'unhandled_rejection',
            reason && reason.message ? reason.message : String(reason || 'Unhandled promise rejection'),
            {
                stack: reason && reason.stack ? reason.stack : ''
            }
        );
    });

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        logger.cleanup();
        stopAdminPanelAutoRefresh();
    });

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
        orderLink: null,
        orderLinks: [],
        allProductsCount: 0,
        userLanguage: 'en',
        languageSource: 'default',
        isSubscribed: true,
        userId: null,
        username: null,  // Store username for display
        botUsername: null,  // Store bot username for deep links
        lastLoggedUserStateId: null,  // Track last logged user state ID to avoid duplicate logging
        history: [],  // Navigation history for back button
        adminStatus: null,
        adminStatusLoaded: false,
        adminStatusLoading: false,
        adminStatusRefreshTimer: null,
        bootFallbackTimer: null,
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
        webappSessionReady: false,
        webappSessionToken: "",
        initialDataLoadFailed: false,  // Track if initial data load failed/timed out
        browserRouteRestoring: false
    };

    const videoPreviewFrameCache = new Map();
    
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
            btn_contacts: 'OUR CONTACTS',
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
            catalog_empty: 'No products have been added yet. Ask the admin to add products from the Telegram channel, then refresh.',
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
            btn_contacts: 'UNSERE KONTAKTE',
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
            btn_contacts: 'NUESTROS CONTACTOS',
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
            btn_contacts: 'NOS CONTACTS',
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
            btn_contacts: 'I NOSTRI CONTATTI',
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
            btn_contacts: 'ONZE CONTACTEN',
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
        if (!langCode) return state.languages[0];
        const baseLang = langCode.split('-')[0];
        return state.languages.find(l => 
            l.code === langCode || l.code.startsWith(baseLang)
        );
    }

    function normalizeSupportedLanguage(langCode) {
        if (!langCode) return null;
        const clean = String(langCode).trim();
        if (!clean) return null;
        const exact = state.languages.find(l => l.code.toLowerCase() === clean.toLowerCase());
        if (exact) return exact.code;
        const baseLang = clean.split('-')[0].toLowerCase();
        const base = state.languages.find(l => l.code.toLowerCase() === baseLang);
        return base ? base.code : null;
    }

    function readStoredLanguage() {
        try {
            return localStorage.getItem(CONFIG.LANGUAGE_STORAGE_KEY);
        } catch (_err) {
            return null;
        }
    }

    function persistLanguage(langCode) {
        try {
            localStorage.setItem(CONFIG.LANGUAGE_STORAGE_KEY, langCode);
        } catch (_err) {
            // Storage can be unavailable in some embedded browsers; the app still works.
        }
    }

    function applyDetectedLanguage() {
        const params = new URLSearchParams(window.location.search || '');
        const pathLanguage = normalizeSupportedLanguage(window.location.pathname.split('/').filter(Boolean)[0]);
        const browserLanguages = Array.isArray(navigator.languages) ? navigator.languages : [];
        const candidates = [
            { source: 'url', value: params.get('lang') || params.get('language') },
            { source: 'url', value: pathLanguage },
            { source: 'stored', value: readStoredLanguage() },
            { source: 'telegram', value: tg.initDataUnsafe?.user?.language_code },
            { source: 'browser', value: browserLanguages[0] || navigator.language || navigator.userLanguage }
        ];

        for (const candidate of candidates) {
            const supported = normalizeSupportedLanguage(candidate.value);
            if (supported) {
                state.userLanguage = supported;
                state.languageSource = candidate.source;
                logger.log('language', 'auto_detected', 'success', '', {
                    language: supported,
                    source: candidate.source
                });
                return;
            }
        }

        state.userLanguage = 'en';
        state.languageSource = 'default';
    }

    function updateTopLanguageSwitch() {
        document.querySelectorAll('.miniapp-language-option').forEach(link => {
            const linkLanguage = normalizeSupportedLanguage(link.dataset.language || link.textContent);
            const isSelected = !!linkLanguage && findLanguageByCode(state.userLanguage)?.code === linkLanguage;
            link.classList.toggle('selected', isSelected);
            if (isSelected) {
                link.setAttribute('aria-current', 'page');
            } else {
                link.removeAttribute('aria-current');
            }
        });
    }

    function canAutoReplaceLanguage() {
        return !['url', 'stored', 'manual', 'bot'].includes(state.languageSource);
    }

    function applyAuthenticatedUserInfo(userInfo, source = 'backend_initData') {
        if (!userInfo || !userInfo.authenticated || !userInfo.user_id) {
            return false;
        }

        state.userId = userInfo.user_id;
        if (userInfo.username) {
            state.username = userInfo.username;
        }

        const backendLanguage = normalizeSupportedLanguage(userInfo.preferred_language || userInfo.language_code);
        if (backendLanguage) {
            state.userLanguage = backendLanguage;
            state.languageSource = userInfo.language_source === 'bot' ? 'bot' : 'telegram';
            persistLanguage(backendLanguage);
            logger.log('language', 'synced_from_bot_preference', 'success', '', {
                language: backendLanguage,
                source: state.languageSource
            });
        }

        logger.log('user_identification', 'user_id_from_backend', 'success', '', {
            user_id: state.userId,
            source,
            username: userInfo.username || 'none'
        });

        return true;
    }

    // Helper: Normalize contact username (remove @ prefix)
    function normalizeContactUsername(contact) {
        if (!contact) return '';
        return contact.startsWith('@') ? contact.slice(1) : contact;
    }

    function normalizeOrderLinks(links) {
        if (!Array.isArray(links)) return [];
        return links
            .map((item) => ({
                label: String(item?.label || '').trim().slice(0, 40),
                url: String(item?.url || '').trim()
            }))
            .filter((item) => item.label && item.url);
    }

    function isSignalContactLink(item) {
        const label = String(item?.label || '').toLowerCase();
        const url = String(item?.url || '').toLowerCase();
        return label.includes('signal') || url.includes('signal.me') || url.includes('signal.org');
    }

    function prioritizeSignalContactLinks(links) {
        return [...links].sort((first, second) => Number(isSignalContactLink(second)) - Number(isSignalContactLink(first)));
    }

    function applyContactSettings(data) {
        if (!data) return;
        state.orderContact = data.order_contact || null;
        state.orderLink = data.order_link || null;
        state.orderLinks = prioritizeSignalContactLinks(normalizeOrderLinks(data.order_links));
        if (!state.orderLinks.length && state.orderLink) {
            state.orderLinks = [{
                label: isSignalContactLink({ url: state.orderLink }) ? 'Signal' : t('btn_contacts'),
                url: state.orderLink
            }];
        }
    }

    function openContactUrl(link, label = '') {
        if (!link && state.orderContact) {
            const contact = state.orderContact.replace(/^@/, '');
            link = `https://t.me/${contact}`;
            logger.log('contacts', 'using_order_contact', 'info', '', { contact: state.orderContact, constructed_link: link });
        }

        if (!link) {
            logger.log('contacts', 'no_link', 'warn', 'No order link or contact configured');
            tg.showAlert('Contact link not configured. Please contact the admin.');
            return;
        }

        logger.log('contacts', 'opening_link', 'info', '', { link: link, label });

        try {
            tg.openLink(link);
        } catch (err) {
            logger.error('contacts_open_link', err.message, { link: link });
            window.open(link, '_blank');
        }
    }

    function renderHomeContactLinks() {
        const contactInfo = document.getElementById('order-contact-info');
        const linksContainer = document.getElementById('order-links');

        if (contactInfo) {
            contactInfo.replaceChildren();
            if (state.orderContact) {
                const username = normalizeContactUsername(state.orderContact);
                contactInfo.append(document.createTextNode(`${t('order_contact_prefix')} `));
                const contactLink = document.createElement('a');
                contactLink.href = `https://t.me/${username}`;
                contactLink.target = '_blank';
                contactLink.rel = 'noopener';
                contactLink.textContent = state.orderContact;
                contactInfo.appendChild(contactLink);
            }
        }

        if (!linksContainer) return;
        linksContainer.replaceChildren();
        if (!state.orderLinks.length) {
            linksContainer.hidden = true;
            return;
        }

        state.orderLinks.forEach((item) => {
            const linkEl = document.createElement('button');
            linkEl.type = 'button';
            linkEl.className = 'action-btn order-link-btn';
            linkEl.dataset.link = item.url;

            const icon = document.createElement('span');
            icon.className = 'btn-icon';
            icon.textContent = isSignalContactLink(item) ? '📡' : '📞';

            const text = document.createElement('span');
            text.className = 'btn-text';
            text.textContent = item.label;

            linkEl.appendChild(icon);
            linkEl.appendChild(text);
            linkEl.addEventListener('click', (event) => {
                event.preventDefault();
                logger.userInteraction('home_contact_link_click', { label: item.label });
                openContactUrl(item.url, item.label);
            });
            linksContainer.appendChild(linkEl);
        });
        linksContainer.hidden = false;
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

    function isPublicBrowserRoutingEnabled() {
        return Boolean(window.history && window.history.pushState && !tg.initData);
    }

    function taxonomySlug(value) {
        return String(value || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '') || 'all';
    }

    function buildCategoryPageUrl(category, subcategory = null) {
        if (!category) return '/';
        const base = `/category/${taxonomySlug(category)}`;
        return subcategory ? `${base}/${taxonomySlug(subcategory)}` : base;
    }

    function currentBrowserPath() {
        return `${window.location.pathname}${window.location.search}${window.location.hash}`;
    }

    function setBrowserRoute(path, routeState = {}, mode = 'push') {
        if (!isPublicBrowserRoutingEnabled() || state.browserRouteRestoring) return;
        const nextPath = path || '/';
        const payload = {
            bradyRoute: true,
            historyDepth: state.history.length,
            ...routeState
        };
        const method = mode === 'replace' || currentBrowserPath() === nextPath ? 'replaceState' : 'pushState';
        window.history[method](payload, '', nextPath);
    }

    function resolveCategoryBySlug(slug) {
        const normalized = taxonomySlug(decodeURIComponent(slug || ''));
        return state.categories.find(category => taxonomySlug(category.name) === normalized) || null;
    }

    function resolveSubcategoryBySlug(category, slug) {
        if (!category || !Array.isArray(category.subcategories)) return null;
        const normalized = taxonomySlug(decodeURIComponent(slug || ''));
        return category.subcategories.find(subcat => taxonomySlug(subcat.name) === normalized) || null;
    }

    function getRouteFromLocation() {
        const params = new URLSearchParams(window.location.search || '');
        const directId = params.get('product_id') || params.get('product');
        if (directId && /^\d+$/.test(directId)) {
            return { appView: 'product-detail', productId: Number(directId) };
        }

        const pathParts = window.location.pathname.split('/').filter(Boolean);

        if (pathParts[0] === 'product' && /^\d+$/.test(pathParts[1] || '')) {
            return { appView: 'product-detail', productId: Number(pathParts[1]) };
        }

        if (pathParts[0] === 'category' && pathParts[1]) {
            return {
                appView: 'products',
                categorySlug: pathParts[1],
                subcategorySlug: pathParts[2] || ''
            };
        }

        const requestedView = params.get('view');
        if (requestedView === 'search' || requestedView === 'language') {
            return { appView: requestedView };
        }

        return { appView: 'welcome' };
    }

    async function restoreBrowserRoute(routeState = null) {
        if (!isPublicBrowserRoutingEnabled()) return false;
        const route = routeState?.bradyRoute ? routeState : getRouteFromLocation();

        if (Number.isInteger(route.historyDepth)) {
            state.history = state.history.slice(0, Math.max(0, route.historyDepth));
        }

        state.browserRouteRestoring = true;
        try {
            if (route.appView === 'product-detail' && route.productId) {
                return await openDeepLinkedProduct('replace', { addToHistory: false, productId: route.productId });
            }

            if (route.appView === 'products' && route.categorySlug) {
                if (!state.categories.length) {
                    await loadCategories(false);
                }
                const category = resolveCategoryBySlug(route.categorySlug);
                if (!category) {
                    showWelcome(false, 'replace');
                    return false;
                }
                const subcategory = route.subcategorySlug ? resolveSubcategoryBySlug(category, route.subcategorySlug) : null;
                await loadProducts(category.name, subcategory ? subcategory.name : null, true, false, 'replace');
                return true;
            }

            if (route.appView === 'subcategories' && route.category) {
                if (!state.categories.length) {
                    await loadCategories(false);
                }
                const category = state.categories.find(item => item.name === route.category) || resolveCategoryBySlug(route.category);
                if (category) {
                    showSubcategories(category, false, null, 'replace');
                    return true;
                }
            }

            if (route.appView === 'categories') {
                if (!state.categories.length) {
                    await loadCategories(false);
                } else {
                    showCategories(false, 'replace');
                }
                return true;
            }

            if (route.appView === 'search') {
                showSearch(false, 'replace');
                return true;
            }

            if (route.appView === 'language') {
                showLanguageSelection(false, 'replace');
                return true;
            }

            showWelcome(false, 'replace');
            return true;
        } finally {
            state.browserRouteRestoring = false;
            updateBackButton();
        }
    }

    function getDeepLinkedProductId() {
        const route = getRouteFromLocation();
        if (route.appView === 'product-detail' && route.productId) {
            return route.productId;
        }

        const params = new URLSearchParams(window.location.search || '');
        const directId = params.get('product_id') || params.get('product');
        if (directId && /^\d+$/.test(directId)) {
            return Number(directId);
        }

        const startParam = (
            params.get('start') ||
            params.get('start_param') ||
            params.get('tgWebAppStartParam') ||
            tg.initDataUnsafe?.start_param ||
            ''
        );
        const match = String(startParam).match(/^product_(\d+)$/);
        return match ? Number(match[1]) : null;
    }

    async function openDeepLinkedProduct(routeMode = 'replace', options = {}) {
        const productId = options.productId || getDeepLinkedProductId();
        if (!productId) {
            return false;
        }

        const headers = {};
        if (tg.initData) {
            headers['X-Telegram-Init-Data'] = tg.initData;
        }

        const lang = state.userLanguage || 'en';
        const url = `/api/products/${productId}?lang=${encodeURIComponent(lang)}`;
        const startTime = Date.now();

        try {
            logger.apiCall('load_deep_link_product', 'started', {
                product_id: productId,
                endpoint: url
            });

            const response = await fetchJsonWithDedup(url, { headers });
            const responseTime = Date.now() - startTime;

            if (!response.ok) {
                logger.apiCall('load_deep_link_product', 'error', {
                    product_id: productId,
                    status: response.status,
                    response_time_ms: responseTime
                });
                return false;
            }

            const product = await response.json();
            applyContactSettings(product);
            if (product.bot) state.botUsername = product.bot;

            logger.apiCall('load_deep_link_product', 'success', {
                product_id: productId,
                response_time_ms: responseTime
            });
            showProductDetail(product, options.addToHistory ?? false, routeMode);
            return true;
        } catch (err) {
            logger.error('load_deep_link_product', err.message, {
                product_id: productId,
                stack: err.stack
            });
            return false;
        }
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
        updateTopLanguageSwitch();
        

        // Update status message if it's showing a translatable key
        const statusEl = document.getElementById('status');
        if (statusEl && statusEl.hasAttribute('data-i18n')) {
            statusEl.textContent = t(statusEl.getAttribute('data-i18n'));
        }
    }

    function hasVisibleView() {
        return Array.from(document.querySelectorAll('.view')).some(view => {
            return getComputedStyle(view).display !== 'none';
        });
    }

    function clearBootFallbackTimer() {
        if (state.bootFallbackTimer) {
            clearTimeout(state.bootFallbackTimer);
            state.bootFallbackTimer = null;
        }
    }

    function scheduleBootFallback() {
        clearBootFallbackTimer();
        state.bootFallbackTimer = setTimeout(() => {
            if (hasVisibleView()) {
                return;
            }

            logger.error('boot_fallback', 'No Mini App view rendered after startup', {
                pathname: window.location.pathname,
                search: window.location.search,
                initData_present: !!tg.initData,
                telegram_available: !!(window.Telegram && window.Telegram.WebApp)
            });

            try {
                showWelcome(false, 'replace');
            } catch (err) {
                logger.error('boot_fallback_show_welcome', err.message, {
                    stack: err.stack
                });
            }
        }, 2500);
    }

    async function init() {
        try {
            scheduleBootFallback();

            applyThemeColors();
            tg.ready();
            tg.expand();
            
            // Wait a bit for Telegram WebApp to populate initDataUnsafe
            // This ensures user data is available before rendering
            await new Promise(resolve => setTimeout(resolve, 100));

            applyDetectedLanguage();
            renderUserInfo();
            
            // Re-check user info multiple times to ensure it gets loaded
            // This handles cases where Telegram WebApp initialization is slow
            let userDataFound = false;
            const checkUserInfo = () => {
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
            const checkWithDelay = (delay) => {
                setTimeout(() => {
                    if (!userDataFound) {
                        checkUserInfo();
                    }
                }, delay);
            };
            
            checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[0]);
            checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[1]);
            checkWithDelay(CONFIG.USER_DATA_CHECK_INTERVALS[2]);
            
            setupEventListeners();
            await establishWebAppSession();
            
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
            queueAdminPanelRefresh();
            startAdminPanelAutoRefresh();
            
            // Open product-specific WebApp links or restore an app route when present.
            const openedRoute = await restoreBrowserRoute(window.history.state);
            const openedDeepLink = openedRoute ? true : await openDeepLinkedProduct('replace', { addToHistory: false });
            if (!openedDeepLink) {
                // Show welcome screen with loaded data (even if data loading failed)
                showWelcome(false, 'replace');
            }
        } catch (err) {
            logger.error('init_boot', err.message, {
                stack: err.stack
            });
            showWelcome(false, 'replace');
        } finally {
            if (hasVisibleView()) {
                clearBootFallbackTimer();
            }
        }
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
            
            const deepLinkedProductId = getDeepLinkedProductId();
            if (deepLinkedProductId) {
                logger.apiCall('load_initial_data', 'skipped', {
                    endpoint: '/api/products',
                    reason: 'deep_link_product',
                    product_id: deepLinkedProductId
                });
            } else {
                logger.apiCall('load_initial_data', 'started', { endpoint: '/api/products' });
                const startTime = Date.now();

                try {
                    const response = await fetch('/api/products?limit=1', { headers });
                    const responseTime = Date.now() - startTime;

                    if (response.ok) {
                        const data = await response.json();
                        applyContactSettings(data);
                        state.botUsername = data.bot || null;
                        logger.apiCall('load_initial_data', 'success', {
                            endpoint: '/api/products',
                            response_time_ms: responseTime,
                            order_contact: state.orderContact,
                            order_link: state.orderLink,
                            order_links_count: state.orderLinks.length,
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
            
            // 2. Always ask the backend when initData is signed.
            // Telegram frontend data has the user id, but only the backend has the
            // bot's stored language preference chosen via /language.
            if (tg.initData) {
                try {
                    const userInfoResponse = await fetch('/api/user/info', {
                        headers,
                        cache: 'no-store',
                        credentials: 'same-origin'
                    });
                    if (userInfoResponse.ok) {
                        const userInfo = await userInfoResponse.json();
                        const applied = applyAuthenticatedUserInfo(userInfo);
                        if (applied) {
                            applyTranslations();
                            renderUserInfo();
                        }
                    } else {
                        logger.apiCall('load_user_info_from_backend', 'error', {
                            endpoint: '/api/user/info',
                            status: userInfoResponse.status
                        });
                    }
                } catch (err) {
                    logger.error('load_user_info_from_backend', err.message, {
                        stack: err.stack
                    });
                }
            }
            
            // 3. Track user and refresh subscription status in the background.
            // These are useful, but they should not delay the first screen render.
            if (state.userId) {
                queueUserBackgroundRefresh();
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

    async function establishWebAppSession() {
        if (!tg.initData || state.webappSessionReady) {
            return state.webappSessionReady;
        }

        try {
            const response = await fetch('/api/webapp-session', {
                method: 'POST',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: { 'X-Telegram-Init-Data': tg.initData }
            });
            state.webappSessionReady = response.ok;
            if (!response.ok) {
                logger.apiCall('webapp_session', 'error', { status: response.status });
            } else {
                try {
                    const payload = await response.json();
                    state.webappSessionToken = typeof payload.session === 'string' ? payload.session : '';
                } catch (parseErr) {
                    state.webappSessionToken = '';
                }
            }
        } catch (err) {
            state.webappSessionReady = false;
            logger.error('webapp_session', err.message);
        }
        return state.webappSessionReady;
    }

    function queueUserBackgroundRefresh() {
        setTimeout(() => {
            trackWebappUser().catch(err => {
                logger.error('user_tracking', 'Failed to track user: ' + err.message);
            });

            loadUserSubscriptionStatus().catch(err => {
                logger.error('load_subscription_status', 'Failed to load subscription: ' + err.message);
            });
        }, 0);
    }

    async function trackWebappUser() {
        if (!state.userId) return;

        const trackHeaders = { 'Content-Type': 'application/json' };
        if (tg.initData) {
            trackHeaders['X-Telegram-Init-Data'] = tg.initData;
        }
        const user = tg.initDataUnsafe?.user || {};
        await fetch('/api/user/track', {
            method: 'POST',
            headers: trackHeaders,
            cache: 'no-store',
            credentials: 'same-origin',
            body: JSON.stringify({
                user_id: state.userId,
                username: state.username || user.username || null,
                first_name: user.first_name || null,
                last_name: user.last_name || null,
                language_code: state.userLanguage,
                language_source: state.languageSource
            })
        });
        logger.log('user_tracking', 'user_tracked_in_db', 'success', '', {
            user_id: state.userId
        });
    }

    async function syncUserLanguagePreference(languageCode, source = 'manual') {
        const supported = normalizeSupportedLanguage(languageCode);
        if (!supported || !tg.initData || !state.userId) return;

        const user = tg.initDataUnsafe?.user || {};
        try {
            const response = await fetch('/api/user/track', {
                method: 'POST',
                cache: 'no-store',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Telegram-Init-Data': tg.initData
                },
                body: JSON.stringify({
                    user_id: state.userId,
                    username: state.username || user.username || null,
                    first_name: user.first_name || null,
                    last_name: user.last_name || null,
                    language_code: supported,
                    language_source: source
                })
            });
            if (!response.ok) {
                logger.apiCall('sync_language_preference', 'error', {
                    status: response.status,
                    language: supported,
                    source
                });
            }
        } catch (err) {
            logger.error('sync_language_preference', err.message, { stack: err.stack });
        }
    }

    async function loadUserSubscriptionStatus() {
        if (!tg.initData || !state.userId) return;
        
        try {
            const headers = { 'X-Telegram-Init-Data': tg.initData };
            const response = await fetch(`/api/user/subscription?user_id=${state.userId}`, {
                headers,
                cache: 'no-store',
                credentials: 'same-origin'
            });
            if (response.ok) {
                const data = await response.json();
                state.isSubscribed = data.is_subscribed !== false;
            }
        } catch (err) {
            logger.error('load_subscription_status', 'Failed to load subscription status: ' + err.message);
        }
    }

    function formatAdminCount(value) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return '0';
        return number.toLocaleString();
    }

    function formatAdminMs(value) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return '0ms';
        return `${Math.round(number)}ms`;
    }

    function formatAdminBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const scaled = bytes / Math.pow(1024, index);
        return `${scaled >= 10 || index === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
    }

    function formatAdminDuration(value) {
        let seconds = Math.max(0, Math.round(Number(value || 0)));
        if (!Number.isFinite(seconds)) return '0s';
        const days = Math.floor(seconds / 86400);
        seconds %= 86400;
        const hours = Math.floor(seconds / 3600);
        seconds %= 3600;
        const minutes = Math.floor(seconds / 60);
        if (days) return `${days}d ${hours}h`;
        if (hours) return `${hours}h ${minutes}m`;
        if (minutes) return `${minutes}m ${seconds % 60}s`;
        return `${seconds}s`;
    }

    function formatAdminPercent(value, digits = 1) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return '0%';
        return `${number.toFixed(digits)}%`;
    }

    function formatAdminPagePath(page) {
        const value = String(page || '').trim();
        if (!value) return '/';
        try {
            const url = new URL(value, window.location.origin);
            return `${url.pathname}${url.search || ''}` || '/';
        } catch (err) {
            return value.replace(/^https?:\/\/[^/]+/i, '') || value;
        }
    }

    function appendAdminMetric(grid, label, value) {
        const item = document.createElement('div');
        item.className = 'admin-metric';

        const labelEl = document.createElement('span');
        labelEl.className = 'admin-metric-label';
        labelEl.textContent = label;

        const valueEl = document.createElement('strong');
        valueEl.textContent = value;

        item.append(labelEl, valueEl);
        grid.appendChild(item);
    }

    function appendAdminList(section, title, items, formatter, limit = 5) {
        if (!items || !items.length) return;

        const group = document.createElement('div');
        group.className = 'admin-list-group';

        const heading = document.createElement('h4');
        heading.textContent = title;
        group.appendChild(heading);

        const list = document.createElement('ul');
        items.slice(0, limit).forEach(item => {
            const li = document.createElement('li');
            li.textContent = formatter(item);
            list.appendChild(li);
        });

        group.appendChild(list);
        section.appendChild(group);
    }

    function appendAdminDetailsList(section, className, title, noteText, items, buildItem, displayCount = null) {
        if (!Array.isArray(items) || !items.length) return;

        const group = document.createElement('div');
        group.className = `admin-list-group ${className}`;
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = `${title} (${formatAdminCount(displayCount ?? items.length)})`;
        details.appendChild(summary);

        if (noteText) {
            const note = document.createElement('p');
            note.className = 'admin-list-note';
            note.textContent = noteText;
            details.appendChild(note);
        }

        const list = document.createElement('ul');
        items.forEach(item => list.appendChild(buildItem(item)));
        details.appendChild(list);
        group.appendChild(details);
        section.appendChild(group);
    }

    function appendAdminGooglePages(section, seo) {
        const items = seo.top_search_console_pages || [];
        if (!items.length) return;

        const totalCount = Number(seo.search_console_pages_window || items.length);
        const visibleItems = items.slice(0, 150);
        appendAdminDetailsList(
            section,
            'admin-google-pages',
            'Google Search Performance Pages',
            `Showing ${formatAdminCount(visibleItems.length)} URL(s) with Google impressions or clicks; this is not the full Google index.`,
            visibleItems,
            item => {
                const li = document.createElement('li');
                const link = document.createElement('a');
                link.href = item.page || '/';
                link.target = '_blank';
                link.rel = 'noopener';
                link.textContent = formatAdminPagePath(item.page);

                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                meta.textContent = `${formatAdminCount(item.clicks)} clicks, ${formatAdminCount(item.impressions)} impressions, ${formatAdminCount(item.query_count)} queries, avg pos ${item.avg_position || '-'}, last ${item.last_seen || '-'}`;
                li.append(link, meta);
                return li;
            },
            totalCount
        );
    }

    function appendAdminCrawlablePages(section, snapshot) {
        const pages = snapshot?.pages || [];
        if (!pages.length) return;

        appendAdminDetailsList(
            section,
            'admin-crawlable-pages',
            'Current Crawlable Site Pages',
            `Showing ${formatAdminCount(snapshot.shown || pages.length)} URL(s) generated by the current catalog and sitemap.`,
            pages,
            item => {
                const li = document.createElement('li');
                const link = document.createElement('a');
                link.href = item.url || item.path || '/';
                link.target = '_blank';
                link.rel = 'noopener';
                link.textContent = `${item.type || 'page'}: ${formatAdminPagePath(item.url || item.path)}`;

                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                meta.textContent = `${item.title || 'Untitled'}${item.lastmod ? `, lastmod ${item.lastmod}` : ''}`;
                li.append(link, meta);
                return li;
            },
            snapshot.total || pages.length
        );
    }


    function renderAdminPanel(data) {
        const panel = document.getElementById('admin-panel');
        if (!panel || !data) return;

        const summary = data.summary || {};
        const seo = data.seo || {};
        const indexnow = data.indexnow || {};
        const googleSitemap = seo.search_console_sitemap || {};
        const searchConsoleSetup = seo.search_console_setup || {};
        const seoRuntime = seo.runtime_status || {};
        const metrics = data.metrics?.endpoints || {};
        const process = data.process || {};
        const mediaRuntime = data.media_runtime || {};
        const cacheState = data.cache_state || {};
        const resourceUsage = data.resource_usage || {};
        const tmpUsage = resourceUsage.tmp || {};
        const diskUsage = resourceUsage.cache || {};
        const webhookMonitor = data.webhook_monitor || {};
        const webhookBotStats = webhookMonitor.bots || {};
        const runtimeBots = Array.isArray(data.runtime_bots) ? data.runtime_bots : [];
        const activeBots = runtimeBots.filter(bot => bot.username && bot.telegram_ok !== false);
        const unhealthyBots = runtimeBots.filter(bot => bot.telegram_health && !['ok', 'webhook_warning'].includes(bot.telegram_health));
        const webhookWarningBots = runtimeBots.filter(bot => bot.telegram_health === 'webhook_warning');
        const setupWarningBots = runtimeBots.filter(bot => Number(bot.startup_issue_count || 0) > 0);
        const googleWindowDays = seo.search_console_window_days || 30;
        const mappingOutcomes = {};
        (seo.mapping_outcomes || []).forEach(item => {
            mappingOutcomes[String(item.status || 'pending')] = Number(item.count || 0);
        });
        const webhookTotals = Object.values(webhookBotStats).reduce((totals, item) => {
            totals.success += Number(item?.success || 0);
            totals.failure += Number(item?.failure || 0);
            totals.total += Number(item?.total || 0);
            return totals;
        }, { success: 0, failure: 0, total: 0 });
        const webhookSuccessPercent = webhookTotals.total
            ? (webhookTotals.success * 100 / webhookTotals.total)
            : 100;
        const visitorQueuePercent = Number(process.visitor_queue_max || 0)
            ? Number(process.visitor_queue_size || 0) * 100 / Number(process.visitor_queue_max)
            : 0;

        panel.replaceChildren();
        panel.hidden = false;

        const header = document.createElement('div');
        header.className = 'admin-panel-header';

        const title = document.createElement('h3');
        title.textContent = 'Admin Monitor';

        const meta = document.createElement('span');
        meta.textContent = data.generated_at
            ? `Updated ${data.generated_at.replace('T', ' ').replace('Z', '')}${Number(data.cache_age_seconds || 0) ? ` · cache ${Number(data.cache_age_seconds).toFixed(1)}s` : ''}`
            : 'Live';

        header.append(title, meta);
        panel.appendChild(header);

        const grid = document.createElement('div');
        grid.className = 'admin-metric-grid';
        appendAdminMetric(grid, 'Bots online', `${formatAdminCount(activeBots.length)}/${formatAdminCount(data.configured_bot_count)}`);
        appendAdminMetric(grid, 'Bot warnings', `${formatAdminCount(unhealthyBots.length)} unhealthy / ${formatAdminCount(webhookWarningBots.length)} webhook / ${formatAdminCount(setupWarningBots.length)} setup`);
        appendAdminMetric(grid, 'Products in / out of stock', `${formatAdminCount(summary.products_in_stock)} / ${formatAdminCount(summary.products_out_of_stock)}`);
        appendAdminMetric(grid, 'Catalog taxonomy', `${formatAdminCount(summary.products)} products · ${formatAdminCount(summary.categories)} categories · ${formatAdminCount(summary.subcategories)} subcategories`);
        appendAdminMetric(grid, 'Users 24h / 7d / total', `${formatAdminCount(summary.active_users_24h)} / ${formatAdminCount(summary.active_users_7d)} / ${formatAdminCount(summary.bot_users)}`);
        appendAdminMetric(grid, 'Subscribed / blocked', `${formatAdminCount(summary.subscribed_users)} / ${formatAdminCount(summary.blocked_users)}`);
        appendAdminMetric(grid, 'Human visitors / pageviews 24h', `${formatAdminCount(summary.unique_visitors_24h)} / ${formatAdminCount(summary.visitor_events_24h)}`);
        appendAdminMetric(grid, 'Website / WebApp views 24h', `${formatAdminCount(summary.website_visits_24h)} / ${formatAdminCount(summary.webapp_visits_24h)}`);
        appendAdminMetric(grid, 'Known Telegram visitors 24h', formatAdminCount(summary.known_visitor_users_24h));
        appendAdminMetric(grid, 'Crawler / search crawler requests', `${formatAdminCount(summary.crawler_events_24h)} / ${formatAdminCount(summary.search_crawler_events_24h)}`);
        appendAdminMetric(grid, 'Pending / stale notifications', `${formatAdminCount(summary.pending_notifications)} / ${formatAdminCount(summary.stale_pending_notifications)}`);
        appendAdminMetric(grid, 'Queued / stale messages', `${formatAdminCount(summary.queued_custom_messages)} / ${formatAdminCount(summary.stale_custom_messages)}`);
        appendAdminMetric(grid, 'WebApp events / errors 24h', `${formatAdminCount(summary.webapp_events_24h)} / ${formatAdminCount(summary.webapp_errors_24h)}`);
        appendAdminMetric(grid, 'Inline queries / chosen 24h', `${formatAdminCount(summary.inline_queries_24h)} / ${formatAdminCount(summary.inline_chosen_24h)}`);
        appendAdminMetric(grid, 'Searches / no-result 24h', `${formatAdminCount(summary.search_queries_24h)} / ${formatAdminCount(summary.search_no_result_24h)}`);
        appendAdminMetric(grid, 'Searches / no-result 7d', `${formatAdminCount(summary.search_queries_7d)} / ${formatAdminCount(summary.search_no_result_7d)}`);
        appendAdminMetric(grid, 'Webhook success', `${formatAdminPercent(webhookSuccessPercent, 1)} · ${formatAdminCount(webhookTotals.failure)} failures`);
        appendAdminMetric(grid, 'Process uptime', formatAdminDuration(process.uptime_seconds));
        appendAdminMetric(grid, 'Memory current / peak', `${formatAdminBytes(process.memory_rss_bytes)} / ${formatAdminBytes(process.memory_peak_rss_bytes)}`);
        appendAdminMetric(grid, 'Persistent cache used / free', `${formatAdminBytes(diskUsage.app_cache_bytes)} / ${formatAdminBytes(diskUsage.free_bytes)}`);
        appendAdminMetric(grid, 'Temporary media / free', `${formatAdminBytes(tmpUsage.used_bytes)} / ${formatAdminBytes(tmpUsage.free_bytes)}`);
        appendAdminMetric(grid, 'Media streams / files', `${formatAdminCount(mediaRuntime.active_streams)} / ${formatAdminCount(mediaRuntime.active_files)}`);
        appendAdminMetric(grid, 'Visitor writer queue', `${formatAdminCount(process.visitor_queue_size)}/${formatAdminCount(process.visitor_queue_max)} (${formatAdminPercent(visitorQueuePercent, 1)}) · ${formatAdminCount(process.visitor_events_dropped)} dropped`);
        appendAdminMetric(grid, `Google performance pages ${googleWindowDays}d`, formatAdminCount(seo.search_console_pages_window));
        appendAdminMetric(grid, 'Google queries / clicks / impressions', `${formatAdminCount(seo.search_console_queries_window)} / ${formatAdminCount(seo.search_console_clicks_window)} / ${formatAdminCount(seo.search_console_impressions_window)}`);
        appendAdminMetric(grid, 'Image/video impressions', `${formatAdminCount(seo.search_console_image_impressions_window)}/${formatAdminCount(seo.search_console_video_impressions_window)}`);
        appendAdminMetric(grid, 'SEO learners', `${seoRuntime.seo_auto_learn_last_status || 'pending'} / ${seoRuntime.seo_trends_last_status || 'pending'}`);
        appendAdminMetric(grid, 'Mapping changes 24h / 7d', `${formatAdminCount(seo.mappings_updated_24h)} / ${formatAdminCount(seo.mappings_updated_7d)}`);
        appendAdminMetric(grid, 'Mapping outcomes', `${formatAdminCount(mappingOutcomes.winner)} winning / ${formatAdminCount(mappingOutcomes.monitoring)} monitoring / ${formatAdminCount(mappingOutcomes.pending)} pending / ${formatAdminCount(mappingOutcomes.retired_no_evidence)} retired`);
        appendAdminMetric(grid, 'Google import', `${searchConsoleSetup.configured ? (seoRuntime.search_console_import_last_status || 'configured') : 'not connected'}${seoRuntime.search_console_import_last_attempt_at ? ` · ${seoRuntime.search_console_import_last_attempt_at}` : ''}`);
        appendAdminMetric(grid, 'Google sitemap', googleSitemap.status || 'never');
        appendAdminMetric(grid, 'IndexNow submitted / pending / failed', `${formatAdminCount(indexnow.submitted)} / ${formatAdminCount(indexnow.pending)} / ${formatAdminCount(indexnow.failed)}`);
        if (Number(data.webhook_401_total || 0) > 0) {
            appendAdminMetric(grid, 'Webhook 401 total', formatAdminCount(data.webhook_401_total));
        }
        panel.appendChild(grid);

        const latency = document.createElement('div');
        latency.className = 'admin-latency-row';
        ['api_products', 'api_categories', 'webhook_401'].forEach(name => {
            const stat = metrics[name];
            if (!stat) return;
            const chip = document.createElement('span');
            chip.textContent = `${name}: avg ${formatAdminMs(stat.avg_ms)} p95 ${formatAdminMs(stat.p95_ms)}`;
            latency.appendChild(chip);
        });
        if (latency.childElementCount) panel.appendChild(latency);

        appendAdminList(panel, 'Runtime Bots', data.runtime_bots || [], item => {
            const username = item.username ? `@${item.username}` : 'not started';
            const health = item.telegram_health || (item.running ? 'running' : 'stopped');
            const slot = item.slot_name || (item.slot_id !== null && item.slot_id !== undefined ? `slot ${item.slot_id}` : 'slot -');
            let detail = `#${item.index} ${username}: ${health}, ${slot}, Telegram id ${item.telegram_bot_id || '-'}, app ${item.running ? 'running' : 'stopped'}`;
            if (item.webhook_pending !== null && item.webhook_pending !== undefined) {
                detail += `, pending ${formatAdminCount(item.webhook_pending)}`;
            }
            if (item.webhook_last_error) {
                detail += `, webhook error: ${String(item.webhook_last_error).slice(0, 90)}`;
            } else if (item.telegram_error) {
                detail += `, Telegram error: ${String(item.telegram_error).slice(0, 90)}`;
            }
            if (Number(item.startup_issue_count || 0) > 0) {
                const issues = Array.isArray(item.startup_issues) ? item.startup_issues : [];
                const issue = issues[0] || {};
                detail += `, setup warnings ${formatAdminCount(item.startup_issue_count)}`;
                if (issue.stage || issue.error) {
                    detail += `: ${String(issue.stage || 'setup')} ${String(issue.error || '').slice(0, 70)}`;
                }
            }
            return detail;
        }, Math.max(20, Number(data.configured_bot_count || 0), runtimeBots.length));
        const setupIssues = [];
        runtimeBots.forEach(bot => {
            const issues = Array.isArray(bot.startup_issues) ? bot.startup_issues : [];
            issues.forEach(issue => {
                setupIssues.push({
                    index: bot.index,
                    username: bot.username,
                    stage: issue.stage,
                    admin_id: issue.admin_id,
                    error: issue.error,
                    recorded_at: issue.recorded_at
                });
            });
        });
        appendAdminList(panel, 'Bot Setup Warnings', setupIssues, item => {
            const label = `#${item.index} ${item.username ? `@${item.username}` : 'not started'}`;
            const admin = item.admin_id ? ` admin ${item.admin_id}` : '';
            return `${label}: ${String(item.stage || 'setup')}${admin} · ${String(item.error || '').slice(0, 120)}`;
        }, 15);
        appendAdminList(panel, 'Pending Notifications By Bot', data.pending_notifications_by_bot || [], item =>
            `@${item.bot_username || 'bot'}: ${formatAdminCount(item.pending)} pending`
        );
        appendAdminList(panel, 'Recent Users', data.recent_users || [], item => {
            const identity = item.username ? `@${item.username}` : `${item.first_name || 'User'} ${item.last_name || ''}`.trim();
            const status = `${item.is_blocked ? 'blocked' : 'active'} · ${item.is_subscribed ? 'subscribed' : 'unsubscribed'} · ${item.notifications_enabled ? 'notifications on' : 'notifications off'}`;
            return `${identity} (${item.user_id || '-'}): @${item.bot_username || 'bot'} · ${item.language || 'en'} · ${formatAdminCount(item.interaction_count)} interactions · ${status} · last ${item.last_seen || '-'}`;
        }, 25);

        appendAdminList(panel, 'Top Google Searches', seo.top_search_console_queries || [], item =>
            `${item.query}: ${formatAdminCount(item.clicks)} clicks, ${formatAdminCount(item.impressions)} impressions, avg pos ${item.avg_position || '-'}`
        );
        appendAdminDetailsList(
            panel,
            'admin-google-page-scorecard',
            'Google Page Performance',
            'Pages with the most Google visibility in the current Search Console window.',
            seo.search_console_page_scorecard || [],
            item => {
                const li = document.createElement('li');
                li.textContent = `${item.page || '/'}: ${formatAdminCount(item.clicks)} clicks, ${formatAdminCount(item.impressions)} impressions`;
                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                meta.textContent = `CTR ${item.ctr_percent || 0}% · avg position ${item.avg_position || '-'} · last ${item.last_seen || '-'}`;
                li.appendChild(meta);
                return li;
            }
        );
        if (googleSitemap.status) {
            appendAdminList(panel, 'Google Sitemap Submission', [googleSitemap], item => {
                const submitted = item.last_submitted_at ? `accepted ${item.last_submitted_at}` : `attempt ${item.last_attempt_at || 'none yet'}`;
                const target = item.sitemap_url || 'sitemap not set';
                const note = item.last_error ? `, ${item.last_error}` : '';
                return `${item.status}: ${submitted}, ${target}${note}`;
            });
        }
        appendAdminList(panel, 'Top Visitor Locations', data.top_visitor_locations || [], item => {
            const place = [item.city, item.region, item.country].filter(part => part && part !== 'Unknown').join(', ') || 'Unknown';
            return `${place}: ${formatAdminCount(item.visitors)} visitors, ${formatAdminCount(item.visits)} visits`;
        });
        appendAdminList(panel, 'Google Ranking Opportunities', seo.search_console_opportunities || [], item =>
            `${item.query || 'query'}: ${formatAdminCount(item.impressions)} impressions, ${formatAdminCount(item.clicks)} clicks, CTR ${Math.round(Number(item.ctr || 0) * 1000) / 10}%, avg pos ${item.avg_position || '-'} · ${item.page || '/'}`
        );

        appendAdminList(panel, 'CTR Terms Being Learned', seo.search_console_ctr_terms || [], item =>
            `${item.query || 'query'}: ${formatAdminCount(item.impressions)} impressions, CTR ${Math.round(Number(item.ctr || 0) * 1000) / 10}%, ${item.category || ''}${item.subcategory ? ` / ${item.subcategory}` : ''}`
        );

        appendAdminDetailsList(
            panel,
            'admin-seo-mapping-changes',
            'SEO Mapping Changes',
            'Newest approved mapping edits. Search usage increases the hit count without changing the edit timestamp.',
            (seo.recent_mappings || []).slice(0, 50),
            item => {
                const li = document.createElement('li');
                const taxonomy = `${item.category || 'catalog'}${item.subcategory ? ` / ${item.subcategory}` : ''}`;
                li.textContent = `${item.term || 'term'} -> ${taxonomy}`;

                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                const evidence = item.outcome_status
                    ? ` · outcome ${item.outcome_status} · impressions ${formatAdminCount(item.baseline_impressions)}→${formatAdminCount(item.evaluation_impressions)} · clicks ${formatAdminCount(item.baseline_clicks)}→${formatAdminCount(item.evaluation_clicks)}`
                    : '';
                meta.textContent = `source ${item.source || 'unknown'} · language ${item.language || 'en'} · ${formatAdminCount(item.hits)} uses${evidence} · changed ${item.updated_at || '-'}`;
                li.appendChild(meta);
                return li;
            }
        );
        appendAdminDetailsList(
            panel,
            'admin-seo-mapping-sources',
            'SEO Mapping Sources',
            'Current approved mapping coverage grouped by the worker or source that created it.',
            seo.mapping_sources || [],
            item => {
                const li = document.createElement('li');
                li.textContent = `${item.source || 'unknown'}: ${formatAdminCount(item.count)} mappings, ${formatAdminCount(item.hits)} uses`;

                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                meta.textContent = `latest change ${item.latest_change || '-'}`;
                li.appendChild(meta);
                return li;
            }
        );
        appendAdminDetailsList(
            panel,
            'admin-seo-worker-activity',
            'SEO Worker Activity',
            'Latest completed background runs and the number of changes each one made.',
            [
                {
                    name: 'Auto learner',
                    status: seoRuntime.seo_auto_learn_last_status,
                    changed: seoRuntime.seo_auto_learn_last_changed,
                    at: seoRuntime.seo_auto_learn_last_run_at,
                    extra: `${formatAdminCount(seoRuntime.seo_auto_learn_last_candidates)} candidates, ${formatAdminCount(seoRuntime.seo_auto_learn_last_suppressed)} suppressed, ${formatAdminCount(seoRuntime.seo_auto_learn_last_retired_noise)} noise retired`,
                    error: seoRuntime.seo_auto_learn_last_error
                },
                {
                    name: 'Google Trends',
                    status: seoRuntime.seo_trends_last_status,
                    changed: seoRuntime.seo_trends_last_promoted,
                    at: seoRuntime.seo_trends_last_run_at,
                    extra: `${formatAdminCount(seoRuntime.seo_trends_last_candidates)} candidates, ${formatAdminCount(seoRuntime.seo_trends_last_imported)} imported, ${formatAdminCount(seoRuntime.seo_trends_last_matured)} matured`,
                    error: seoRuntime.seo_trends_last_error
                },
                {
                    name: 'Search Console',
                    status: seoRuntime.search_console_import_last_status,
                    changed: seoRuntime.search_console_import_last_signals,
                    at: seoRuntime.search_console_import_last_attempt_at,
                    extra: `${formatAdminCount(seoRuntime.search_console_import_last_rows)} rows, ${formatAdminCount(seoRuntime.search_console_import_last_media_rows)} media rows, ${formatAdminCount(seoRuntime.search_console_import_last_aggregates)} aggregates · ${seoRuntime.search_console_import_last_auth_source || 'no auth source'}`,
                    error: seoRuntime.search_console_import_last_error
                }
            ],
            item => {
                const li = document.createElement('li');
                li.textContent = `${item.name}: ${item.status || 'pending'} · ${formatAdminCount(item.changed)} changes`;

                const meta = document.createElement('span');
                meta.className = 'admin-page-meta';
                meta.textContent = `${item.extra} · last ${item.at || '-'}${item.error ? ` · error ${String(item.error).slice(0, 180)}` : ''}`;
                li.appendChild(meta);
                return li;
            }
        );
        appendAdminDetailsList(panel, 'admin-public-pages', 'Top Public Pages', 'Real visitor activity; crawler traffic is excluded.', data.top_public_pages || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.page_type || item.source} ${item.path || '/'}: ${formatAdminCount(item.visits)} visits, ${formatAdminCount(item.visitors)} visitors`;
            return li;
        });
        appendAdminDetailsList(panel, 'admin-media-pages', 'Google Image & Video Pages', 'Pages appearing in Google image or video search.', seo.top_search_console_media_pages || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.search_type || 'media'}: ${formatAdminCount(item.impressions)} impressions, ${formatAdminCount(item.clicks)} clicks · ${item.page || '/'}`;
            return li;
        });

        const endpointPerformance = Object.entries(metrics)
            .map(([name, item]) => ({ name, ...(item || {}) }))
            .sort((a, b) => Number(b.p95_ms || 0) - Number(a.p95_ms || 0));
        appendAdminDetailsList(panel, 'admin-endpoint-performance', 'All Endpoint Performance', 'Per-process counters since the latest deploy, ordered by slowest p95.', endpointPerformance, item => {
            const li = document.createElement('li');
            li.textContent = `${item.name}: ${formatAdminCount(item.count)} calls · avg ${formatAdminMs(item.avg_ms)} · p95 ${formatAdminMs(item.p95_ms)}`;
            return li;
        });

        const webhookPerformance = Object.entries(webhookBotStats).map(([index, item]) => ({ index, ...(item || {}) }));
        appendAdminDetailsList(panel, 'admin-webhook-performance', 'Webhook Performance By Bot', 'Successful and failed webhook updates recorded since the latest deploy.', webhookPerformance, item => {
            const li = document.createElement('li');
            const rate = Number(item.success_rate === undefined ? 1 : item.success_rate) * 100;
            li.textContent = `Bot #${item.index}: ${formatAdminCount(item.success)} successful / ${formatAdminCount(item.failure)} failed · ${formatAdminPercent(rate, 1)} success · avg ${formatAdminMs(item.avg_duration_ms)}`;
            const meta = document.createElement('span');
            meta.className = 'admin-page-meta';
            meta.textContent = `last update ${item.last_seen ? new Date(Number(item.last_seen) * 1000).toLocaleString() : '-'}`;
            li.appendChild(meta);
            return li;
        });

        appendAdminDetailsList(panel, 'admin-bot-users', 'Users By Bot', 'Persistent user ownership, recent activity, and blocked-user counts for every bot.', data.bot_user_counts || [], item => {
            const li = document.createElement('li');
            li.textContent = `@${item.bot_username || 'untracked'}: ${formatAdminCount(item.users)} users · ${formatAdminCount(item.active_24h)} active 24h · ${formatAdminCount(item.blocked)} blocked`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-inline-activity', 'Inline Mode Activity', 'Query volume, response latency, and average result count during the last 24 hours.', data.inline_activity || [], item => {
            const li = document.createElement('li');
            li.textContent = `@${item.bot_username || 'unknown'}: ${formatAdminCount(item.queries)} queries · avg ${formatAdminMs(item.avg_latency_ms)} · ${Number(item.avg_results || 0).toFixed(1)} results/query`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-pending-messages', 'Pending Custom Messages By Bot', 'Queued direct messages which have not yet been marked sent.', data.pending_custom_messages_by_bot || [], item => {
            const li = document.createElement('li');
            li.textContent = `@${item.bot_username || 'untracked'}: ${formatAdminCount(item.pending)} pending`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-webapp-actions', 'WebApp Actions 24h', 'Most frequent Mini App events grouped by event, action, and status.', data.webapp_event_counts || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.event_type || 'event'} / ${item.action || 'action'} / ${item.status || 'unknown'}: ${formatAdminCount(item.count)}`;
            return li;
        });

        const recentWebAppProblems = (data.recent_webapp_events || []).filter(item =>
            String(item.status || '').toLowerCase() === 'error' || String(item.error || '').trim()
        );
        appendAdminDetailsList(panel, 'admin-webapp-errors', 'Recent WebApp Errors', 'Latest client-reported WebApp failures, including the user and action when available.', recentWebAppProblems, item => {
            const li = document.createElement('li');
            const identity = item.username ? `@${item.username}` : (item.user_id || 'unknown user');
            li.textContent = `${item.created_at || '-'} · ${identity} · ${item.event_type || 'event'} / ${item.action || 'action'}: ${String(item.error || item.status || 'error').slice(0, 180)}`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-visitor-devices', 'Visitor Devices 24h', 'Human pageviews only; crawlers and automated clients are excluded.', data.visitor_devices || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.device_type || 'unknown'}: ${formatAdminCount(item.visitors)} visitors · ${formatAdminCount(item.visits)} visits`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-recent-visitors', 'Recent Human Visits', 'Latest identified website and Mini App pageviews. Crawler traffic is excluded.', data.recent_visitor_events || [], item => {
            const li = document.createElement('li');
            const identity = item.username ? `@${item.username}` : (item.telegram_user_id || 'anonymous');
            const location = [item.city, item.region, item.country].filter(value => value && value !== 'Unknown').join(', ') || 'unknown location';
            li.textContent = `${item.created_at || '-'} · ${identity} · ${item.source || 'site'} ${item.page_type || 'page'} ${item.path || '/'} · ${location} · ${item.device_type || 'unknown device'}`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-crawlers', 'Crawler Activity 24h', 'Kept separate from human visitor totals.', data.crawler_activity || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.crawler_name || 'Unknown crawler'} (${item.traffic_class || 'automation'}): ${formatAdminCount(item.requests)} requests · ${formatAdminCount(item.pages)} pages · last ${item.last_seen || '-'}`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-recent-products', 'Recently Added Products', 'Newest catalog records and their owning bot.', data.recent_products || [], item => {
            const li = document.createElement('li');
            const title = String(item.caption || '').split('\n')[0] || `Product ${item.id}`;
            li.textContent = `#${item.id} ${title.slice(0, 90)} · ${item.category || 'uncategorized'}${item.subcategory ? ` / ${item.subcategory}` : ''} · ${item.file_type || 'no media'} · @${item.bot_username || 'unknown'}`;
            return li;
        });

        const storageRows = [
            `Process started: ${process.started_at || '-'} · uptime ${formatAdminDuration(process.uptime_seconds)}`,
            `Memory: ${formatAdminBytes(process.memory_rss_bytes)} current · ${formatAdminBytes(process.memory_peak_rss_bytes)} peak · ${formatAdminBytes(process.memory_virtual_bytes)} virtual`,
            `Persistent cache: ${formatAdminBytes(diskUsage.app_cache_bytes)} app files · ${formatAdminBytes(diskUsage.free_bytes)} filesystem free of ${formatAdminBytes(diskUsage.total_bytes)}`,
            `Temporary storage: ${formatAdminBytes(tmpUsage.used_bytes)} app/bot files · ${formatAdminBytes(tmpUsage.free_bytes)} filesystem free`,
            `Media cache: ${mediaRuntime.cache_enabled ? 'enabled' : 'disabled'} · videos ${mediaRuntime.video_cache_enabled ? 'enabled' : 'disabled'} · ${formatAdminCount(mediaRuntime.cache_max_mb)} MB max · ${formatAdminCount(mediaRuntime.cache_ttl_hours)}h TTL`,
            `Media runtime: ${formatAdminCount(mediaRuntime.active_streams)} streams · ${formatAdminCount(mediaRuntime.tracked_temp_files)} tracked temp files · ${formatAdminCount(mediaRuntime.generation_locks)} generation locks`,
            `Visitor writer: ${formatAdminCount(process.visitor_events_written)} written · ${formatAdminCount(process.visitor_events_dropped)} dropped · queue ${formatAdminCount(process.visitor_queue_size)}/${formatAdminCount(process.visitor_queue_max)}`,
            `Response caches: ${formatAdminCount(process.seo_response_cache_entries)} SEO · ${formatAdminCount(process.webapp_search_cache_entries)} WebApp search`
        ];
        appendAdminDetailsList(panel, 'admin-system-resources', 'System & Storage', 'Live process, RAM, disk, media-cache, and background-writer state.', storageRows, item => {
            const li = document.createElement('li');
            li.textContent = item;
            return li;
        });

        const cacheRows = [
            `Resolved file IDs: ${formatAdminCount(cacheState.resolved_file_ids)} memory / ${formatAdminCount(summary.file_id_cache_entries)} persistent`,
            `Media metadata: ${formatAdminCount(cacheState.media_info)} memory · canonical ${formatAdminCount(summary.canonical_media)} · bot mappings ${formatAdminCount(summary.bot_media_mappings)}`,
            `Media objects: ${formatAdminCount(summary.media_objects_active)} active · registry write keys ${formatAdminCount(cacheState.media_registry_write_keys)}`,
            `API result cache: ${formatAdminCount(cacheState.api_results)} · translation cache ${formatAdminCount(summary.translation_cache_entries)}`,
            `Webhook locks: ${formatAdminCount(cacheState.webhook_processing_locks)} · concurrency ${formatAdminCount(cacheState.webhook_update_concurrency)} · orphan legacy secrets ${formatAdminCount(cacheState.orphan_legacy_secrets)}`,
            `Security records: ${formatAdminCount(summary.blocked_entities)} permanent entities · ${formatAdminCount(summary.blocked_ips)} IPs`
        ];
        appendAdminDetailsList(panel, 'admin-cache-inventory', 'Cache & Security Inventory', 'Live and persistent cache sizes plus blocked-access records.', cacheRows, item => {
            const li = document.createElement('li');
            li.textContent = item;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-mapping-languages', 'SEO Mapping Languages', 'Approved mapping coverage by language.', seo.mapping_languages || [], item => {
            const li = document.createElement('li');
            li.textContent = `${String(item.language || 'en').toUpperCase()}: ${formatAdminCount(item.count)} mappings`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-search-languages', 'User Search Languages 30d', 'Languages recorded for catalog searches during the last 30 days.', seo.search_languages_30d || [], item => {
            const li = document.createElement('li');
            li.textContent = `${String(item.language || 'en').toUpperCase()}: ${formatAdminCount(item.count)} searches`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-google-countries', 'Google Performance By Country', `Search Console dimensions for the latest ${googleWindowDays} days.`, seo.search_console_countries || [], item => {
            const li = document.createElement('li');
            li.textContent = `${String(item.country || 'unknown').toUpperCase()}: ${formatAdminCount(item.clicks)} clicks · ${formatAdminCount(item.impressions)} impressions · ${item.ctr_percent || 0}% CTR`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-google-devices', 'Google Performance By Device', `Search Console dimensions for the latest ${googleWindowDays} days.`, seo.search_console_devices || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.device || 'unknown'}: ${formatAdminCount(item.clicks)} clicks · ${formatAdminCount(item.impressions)} impressions · ${item.ctr_percent || 0}% CTR`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-unmapped-searches', 'Low/No-result Searches To Learn', `Recent searches returning at most ${formatAdminCount(seo.low_result_threshold)} products.`, seo.unmapped || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.query || 'query'}: ${formatAdminCount(item.count)} searches · ${formatAdminCount(item.no_result_count)} no-result · last ${item.last_seen || '-'}`;
            return li;
        });

        appendAdminDetailsList(panel, 'admin-external-signals', 'Recent Approved SEO Signals', 'Search Console and Google Trends signals admitted by the relevance safeguards.', seo.recent_external_signals || [], item => {
            const li = document.createElement('li');
            li.textContent = `${item.term || 'term'} -> ${item.category || 'catalog'}${item.subcategory ? ` / ${item.subcategory}` : ''} · ${item.provider || item.source || 'unknown'} · ${item.language || 'en'} · confidence ${formatAdminCount(item.confidence)} · evidence ${formatAdminCount(item.evidence_count)}`;
            return li;
        });

        if (indexnow.last) {
            appendAdminDetailsList(panel, 'admin-indexnow-last', 'Latest IndexNow Submission', 'Most recently updated Bing/IndexNow queue item.', [indexnow.last], item => {
                const li = document.createElement('li');
                li.textContent = `${item.status || 'unknown'} · HTTP ${item.http_status || '-'} · ${formatAdminCount(item.attempts)} attempts · ${item.reason || 'catalog change'} · ${item.url || '-'}`;
                if (item.last_error) {
                    const meta = document.createElement('span');
                    meta.className = 'admin-page-meta';
                    meta.textContent = `error ${String(item.last_error).slice(0, 180)} · updated ${item.updated_at || '-'}`;
                    li.appendChild(meta);
                }
                return li;
            });
        }
        appendAdminGooglePages(panel, seo);
        appendAdminCrawlablePages(panel, data.crawlable_pages);
    }

    async function loadAdminPanel(force = false) {
        if ((!tg.initData && !state.webappSessionReady) || state.adminStatusLoading || (!force && state.adminStatusLoaded)) return;
        state.adminStatusLoading = true;

        try {
            const headers = {};
            if (tg.initData) {
                headers['X-Telegram-Init-Data'] = tg.initData;
            }
            const fetchOptions = {
                headers,
                credentials: 'same-origin',
                cache: 'no-store'
            };
            let response = await fetch(`/api/admin/status?ts=${Date.now()}`, fetchOptions);
            if (response.status === 404) {
                response = await fetch(`/api/webapp-admin/status?ts=${Date.now()}`, fetchOptions);
            }
            if (!response.ok) {
                state.adminStatusLoaded = response.status === 403;
                return;
            }
            const data = await response.json();
            state.adminStatus = data;
            state.adminStatusLoaded = true;
            renderAdminPanel(data);
            logger.log('admin_monitor', 'loaded', 'success', '', {
                products: data.summary?.products,
                google_queries_window: data.seo?.search_console_queries_window
            });
        } catch (err) {
            logger.error('admin_monitor', err.message);
        } finally {
            state.adminStatusLoading = false;
        }
    }

    function queueAdminPanelRefresh() {
        if (!tg.initData && !state.webappSessionReady) return;
        setTimeout(() => {
            loadAdminPanel(true).catch(err => logger.error('admin_monitor', err.message));
        }, 0);
    }

    function startAdminPanelAutoRefresh() {
        if ((!tg.initData && !state.webappSessionReady) || state.adminStatusRefreshTimer) return;
        state.adminStatusRefreshTimer = setInterval(() => {
            if (!document.hidden) {
                loadAdminPanel(true).catch(err => logger.error('admin_monitor', err.message));
            }
        }, 60000);
    }

    function stopAdminPanelAutoRefresh() {
        if (state.adminStatusRefreshTimer) {
            clearInterval(state.adminStatusRefreshTimer);
            state.adminStatusRefreshTimer = null;
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
            
            // Get user language from Telegram unless URL/storage/manual choice won.
            const telegramLanguage = normalizeSupportedLanguage(user.language_code);
            if (telegramLanguage && canAutoReplaceLanguage()) {
                state.userLanguage = telegramLanguage;
                state.languageSource = 'telegram';
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
        updateTopLanguageSwitch();
    }

    function setupEventListeners() {
        if (isPublicBrowserRoutingEnabled()) {
            window.addEventListener('popstate', (event) => {
                restoreBrowserRoute(event.state).catch(err => {
                    logger.error('restore_browser_route', err.message, { stack: err.stack });
                });
            });
        }

        // Header buttons
        document.getElementById('home-btn').addEventListener('click', () => showWelcome());
        document.getElementById('search-btn').addEventListener('click', () => showSearch());
        document.getElementById('language-btn').addEventListener('click', () => showLanguageSelection());
        document.querySelectorAll('.miniapp-language-option').forEach(link => {
            link.addEventListener('click', (event) => {
                const selectedLanguage = normalizeSupportedLanguage(link.dataset.language || link.textContent);
                const language = selectedLanguage ? findLanguageByCode(selectedLanguage) : null;
                if (!language) return;
                event.preventDefault();
                selectLanguage(language, { closeSelection: false, source: 'top_switch' });
            });
        });

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
            '.miniapp-language-option',
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

        if (
            isPublicBrowserRoutingEnabled() &&
            state.history.length > 0 &&
            !state.browserRouteRestoring &&
            window.history.state?.bradyRoute &&
            currentBrowserPath() !== '/'
        ) {
            window.history.back();
            return;
        }
        
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
                restoreProductsView(previous);
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

    function restoreProductsView(previous) {
        state.currentCategory = previous.category || null;
        state.currentSubcategory = previous.subcategory || null;
        state.page = previous.page || 0;
        state.view = 'products';
        setBrowserRoute(
            buildCategoryPageUrl(state.currentCategory, state.currentSubcategory),
            { appView: 'products', category: state.currentCategory, subcategory: state.currentSubcategory },
            'replace'
        );

        const grid = document.getElementById('products-grid');
        if (!state.products.length || !grid || grid.children.length === 0) {
            loadProducts(state.currentCategory, state.currentSubcategory, false, false);
            return;
        }

        hideAllViews();
        document.getElementById('products-view').style.display = 'block';
        updateBreadcrumb();
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

    function showWelcome(addToHistory = true, routeMode = null) {
        if (addToHistory && state.view !== 'welcome') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }
        
        state.view = 'welcome';
        setBrowserRoute('/', { appView: 'welcome' }, routeMode || (addToHistory ? 'push' : 'replace'));
        hideAllViews();
        document.getElementById('welcome-view').style.display = 'block';
        document.getElementById('breadcrumb').style.display = 'none';
        
        renderHomeContactLinks();

        if (state.adminStatus) {
            renderAdminPanel(state.adminStatus);
        } else {
            queueAdminPanelRefresh();
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

    function showLanguageSelection(addToHistory = true, routeMode = null) {
        if (addToHistory && state.view !== 'language') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }
        
        state.view = 'language';
        setBrowserRoute('/?view=language', { appView: 'language' }, routeMode || (addToHistory ? 'push' : 'replace'));
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

    function selectLanguage(lang, options = {}) {
        const source = options.source || (state.view === 'language' ? 'language_screen' : 'manual');
        state.userLanguage = lang.code;
        state.languageSource = 'manual';
        persistLanguage(lang.code);
        syncUserLanguagePreference(lang.code, source);
        
        // Apply translations immediately
        applyTranslations();
        
        setStatus(t('language_changed', lang.name));
        
        updateTopLanguageSwitch();
        renderUserInfo();

        // Close language selection view immediately when using the full language screen.
        if (options.closeSelection !== false && state.view === 'language') {
            goBack();
        }
        
        // Reload the currently visible data with the new language in background.
        // The top language row should not force users out of the page they are viewing.
        setTimeout(async () => {
            try {
                if (state.view === 'categories') {
                    await loadCategories(false, 'replace');
                } else if (state.view === 'products' && (state.currentCategory || state.currentSubcategory)) {
                    // Reload products in current category/subcategory - don't add to history
                    await loadProducts(state.currentCategory, state.currentSubcategory, false, false);
                } else if (state.view === 'search' && state.searchQuery) {
                    // Re-run search with new language
                    await performSearch();
                } else if (state.view === 'welcome') {
                    showWelcome(false, 'replace');
                }
            } catch (error) {
                logger.error('language_reload_failed', error.message, { stack: error.stack });
                setStatus(t('error_load_categories'), true);
            }
        }, 500);
    }

    function showSearch(addToHistory = true, routeMode = null) {
        if (addToHistory && state.view !== 'search') {
            pushHistory({ view: state.view, category: state.currentCategory, subcategory: state.currentSubcategory, page: state.page });
        }

        logger.navigationChange('show_search', {
            from_view: state.view,
            category: state.currentCategory,
            subcategory: state.currentSubcategory
        });

        state.view = 'search';
        setBrowserRoute('/?view=search', { appView: 'search' }, routeMode || (addToHistory ? 'push' : 'replace'));
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
            
            applyContactSettings(data);
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

    async function loadCategories(addToHistory = true, routeMode = null) {
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
            
            showCategories(addToHistory, routeMode);
        } catch (err) {
            logger.error('load_categories_failed', err.message, { stack: err.stack });
            setStatus(t('error_load_categories'), true);
            // Still show the categories view even if load fails, just with empty state
            showCategories(addToHistory, routeMode);
        }
    }

    function showCategories(addToHistory = true, routeMode = null) {
        if (addToHistory && state.view !== 'categories') {
            pushHistory({ view: state.view });
        }
        
        state.view = 'categories';
        state.currentCategory = null;
        state.currentSubcategory = null;
        state.page = 0;
        setBrowserRoute('/', { appView: 'categories' }, routeMode || (addToHistory ? 'push' : 'replace'));

        hideAllViews();
        document.getElementById('categories-view').style.display = 'block';
        
        document.getElementById('categories-title').textContent = t('categories_title');
        document.getElementById('categories-count').textContent = t('categories_count', state.categories.length);
        updateBreadcrumb();

        const grid = document.getElementById('categories-grid');
        grid.replaceChildren();

        // Use DocumentFragment for batch DOM operations - improves rendering performance
        const fragment = document.createDocumentFragment();

        if (state.allProductsCount <= 0) {
            const emptyState = document.createElement('div');
            emptyState.className = 'empty-state';
            emptyState.innerHTML = `
                <h3>${t('search_no_results')}</h3>
                <p>${t('catalog_empty')}</p>
            `;
            grid.appendChild(emptyState);
            setStatus(t('catalog_empty'));
            return;
        }

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

    function showSubcategories(category, addToHistory = true, previousCategory = null, routeMode = null) {
        if (addToHistory) {
            // Save the previous state (before we showed subcategories)
            pushHistory({ view: 'categories', category: previousCategory });
        }

        state.view = 'categories';  // Reuse categories view for subcategories
        state.currentCategory = category.name;
        state.currentSubcategory = null;  // Clear subcategory when entering subcategory selection view
        setBrowserRoute(
            buildCategoryPageUrl(category.name),
            { appView: 'subcategories', category: category.name },
            routeMode || (addToHistory ? 'push' : 'replace')
        );

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
    async function loadProducts(category, subcategory, resetPage = true, addToHistory = true, routeMode = null) {
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
        setBrowserRoute(
            buildCategoryPageUrl(category, subcategory),
            { appView: 'products', category, subcategory },
            routeMode || (addToHistory ? 'push' : 'replace')
        );
        
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
            
            // Use short-lived page cache so back/forward category browsing stays responsive.
            if (cached && cacheAge < CONFIG.PRODUCT_CACHE_TTL_MS) {
                logger.apiCall('load_products', 'cache_hit', { 
                    url, 
                    cache_age_ms: cacheAge,
                    items_count: cached.data.items?.length || 0
                });
                state.products = cached.data.items || [];
                state.totalProducts = cached.data.total || 0;
                applyContactSettings(cached.data);
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
            applyContactSettings(data);
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
                const subcatData = categoryData?.subcategories?.find(s => s.name === state.currentSubcategory);
                title = subcatData ? subcatData.display_name : state.currentSubcategory;
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

    function getVideoPreviewSeekTime(video) {
        if (!Number.isFinite(video.duration) || video.duration <= 0) {
            return CONFIG.VIDEO_THUMBNAIL_SEEK_TIME;
        }
        if (video.duration < CONFIG.VIDEO_SHORT_DURATION_THRESHOLD) {
            return Math.max(0, video.duration * CONFIG.VIDEO_THUMBNAIL_SEEK_PERCENT);
        }
        const seekTime = Math.min(
            CONFIG.VIDEO_THUMBNAIL_SEEK_TIME,
            video.duration * CONFIG.VIDEO_THUMBNAIL_SEEK_PERCENT
        );
        return Math.max(0, Math.min(seekTime, video.duration - CONFIG.VIDEO_BOUNDS_BUFFER));
    }

    function getVideoPreviewFrame(cacheKey) {
        if (!cacheKey || !videoPreviewFrameCache.has(cacheKey)) {
            return null;
        }
        const cached = videoPreviewFrameCache.get(cacheKey);
        videoPreviewFrameCache.delete(cacheKey);
        videoPreviewFrameCache.set(cacheKey, cached);
        return cached;
    }

    function rememberVideoPreviewFrame(cacheKey, src) {
        if (!cacheKey || !src) return;
        if (videoPreviewFrameCache.has(cacheKey)) {
            videoPreviewFrameCache.delete(cacheKey);
        }
        videoPreviewFrameCache.set(cacheKey, src);
        while (videoPreviewFrameCache.size > CONFIG.MAX_VIDEO_PREVIEW_CACHE_SIZE) {
            const oldestKey = videoPreviewFrameCache.keys().next().value;
            videoPreviewFrameCache.delete(oldestKey);
        }
    }

    function createPreviewImage(src) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = 'Video preview';
        img.className = 'video-preview loaded';
        img.loading = 'eager';
        return img;
    }

    function showBrandMediaFallback(container, alt = 'Media unavailable') {
        if (!container) return;
        const img = document.createElement('img');
        img.src = CONFIG.SITE_ICON_URL;
        img.alt = alt;
        img.className = 'brand-media-fallback';
        img.loading = 'eager';
        container.classList.add('placeholder');
        container.replaceChildren(img);
    }

    function videoPreviewCacheKey(productId, mediaIndex) {
        return `${productId}:${mediaIndex}`;
    }

    function safeLogUrl(url) {
        try {
            return new URL(url, window.location.origin).pathname;
        } catch (err) {
            return String(url || '').split('?')[0];
        }
    }

    function teardownMediaViewer(container) {
        if (!container) return;
        container.querySelectorAll('video').forEach((video) => {
            try {
                video.pause();
                video.removeAttribute('src');
                video.load();
            } catch (err) {
                // Ignore teardown failures; the element is being replaced.
            }
        });
        container.replaceChildren();
    }

    function createVideoPreview(mediaUrl, fallbackContainer, cacheKey = '', fallbackImageUrl = '') {
        const cachedPreview = getVideoPreviewFrame(cacheKey);
        if (cachedPreview) {
            return createPreviewImage(cachedPreview);
        }

        const vid = document.createElement('video');
        vid.src = mediaUrl;
        vid.crossOrigin = 'anonymous';
        vid.muted = true;
        vid.playsInline = true;
        vid.setAttribute('playsinline', '');
        vid.autoplay = true;
        vid.preload = 'auto';
        vid.className = 'video-preview';
        if (fallbackImageUrl) {
            vid.poster = fallbackImageUrl;
        }

        let previewLoaded = false;
        let frameCaptured = false;
        let fallbackShown = false;
        const useFallbackImage = () => {
            if (fallbackShown || !fallbackImageUrl || !fallbackContainer) return;
            fallbackShown = true;
            const img = createPreviewImage(fallbackImageUrl);
            img.alt = 'Product preview';
            if (vid.parentNode) {
                vid.parentNode.replaceChild(img, vid);
            } else {
                fallbackContainer.replaceChildren(img);
            }
        };
        const markLoaded = () => {
            if (previewLoaded) return;
            previewLoaded = true;
            vid.classList.add('loaded');
            vid.style.opacity = '1';
        };
        const capturePreviewFrame = () => {
            if (frameCaptured || !fallbackContainer || !vid.videoWidth || !vid.videoHeight) {
                markLoaded();
                return;
            }
            try {
                const canvas = document.createElement('canvas');
                canvas.width = vid.videoWidth;
                canvas.height = vid.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);

                const previewSrc = canvas.toDataURL('image/jpeg', 0.82);
                rememberVideoPreviewFrame(cacheKey, previewSrc);
                const img = createPreviewImage(previewSrc);

                if (vid.parentNode) {
                    vid.parentNode.replaceChild(img, vid);
                }
                frameCaptured = true;
            } catch (err) {
                markLoaded();
            }
        };

        vid.addEventListener('loadedmetadata', () => {
            try {
                vid.currentTime = getVideoPreviewSeekTime(vid);
            } catch (err) {
                markLoaded();
            }
        }, { once: true });
        vid.addEventListener('canplay', capturePreviewFrame, { once: true });
        vid.addEventListener('seeked', capturePreviewFrame, { once: true });
        vid.addEventListener('loadeddata', () => {
            if (vid.currentTime > 0.05 || !Number.isFinite(vid.duration)) {
                capturePreviewFrame();
                markLoaded();
            }
        });
        vid.addEventListener('playing', () => {
            if (typeof vid.requestVideoFrameCallback === 'function') {
                try {
                    vid.requestVideoFrameCallback(() => capturePreviewFrame());
                } catch (err) {
                    capturePreviewFrame();
                }
            }
        }, { once: true });
        vid.onerror = () => {
            if (fallbackContainer) {
                useFallbackImage();
                if (!fallbackShown) showBrandMediaFallback(fallbackContainer, 'Video unavailable');
            }
        };
        setTimeout(() => {
            if (!frameCaptured && !fallbackShown) {
                if (fallbackImageUrl) useFallbackImage();
                markLoaded();
            }
        }, 1200);
        setTimeout(() => {
            if (!frameCaptured && !fallbackShown) {
                if (fallbackImageUrl) useFallbackImage();
                markLoaded();
            }
        }, 2500);

        return vid;
    }

    function createProductCard(item) {
        // NOTE: All <img> elements created here must include an alt attribute for SEO and accessibility.
        const card = document.createElement('a');
        card.className = 'product-card';
        card.href = buildProductPageUrl(item);
        card.addEventListener('click', (event) => {
            if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey
            ) {
                return;
            }
            event.preventDefault();
            showProductDetail(item);
        });
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

        // Determine the media type for the selected index
        const selectedMediaType = (item.media_info && Array.isArray(item.media_info) && item.media_info[mediaIndex])
            ? item.media_info[mediaIndex].type
            : (item.file_type || 'photo');

        const mediaUrl = buildMediaUrl(item, mediaIndex);
        if (mediaUrl) {
            if (selectedMediaType === 'video') {
                const vid = createVideoPreview(
                    mediaUrl,
                    media,
                    videoPreviewCacheKey(item.id, mediaIndex)
                );
                media.appendChild(vid);

                // Play icon overlay for video cards
                const playOverlay = document.createElement('div');
                playOverlay.className = 'card-video-play-overlay';
                playOverlay.textContent = '▶️';
                media.appendChild(playOverlay);
            } else {
                const img = document.createElement('img');
                img.src = mediaUrl;
                img.alt = item.caption || 'Product';
                img.loading = 'lazy';
                img.onload = () => {
                    img.classList.add('loaded');
                };
                img.onerror = () => {
                    showBrandMediaFallback(media);
                };
                media.appendChild(img);
            }
        } else {
            showBrandMediaFallback(media, 'No product media');
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
        if ((item.stock_status || 'active') === 'out_of_stock') {
            const stockBadge = document.createElement('div');
            stockBadge.className = 'stock-badge';
            stockBadge.textContent = 'Sold out';
            body.appendChild(stockBadge);
        }
        body.appendChild(footer);

        card.appendChild(media);
        card.appendChild(body);

        return card;
    }

    function buildProductPageUrl(item) {
        if (!item || !item.id) return '#';
        const firstLine = String(item.caption || 'product').split('\n')[0].trim().toLowerCase();
        const slug = firstLine
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '')
            .slice(0, 60)
            .replace(/-+$/g, '') || 'product';
        return `/product/${item.id}/${slug}`;
    }

    function buildMediaUrl(item, mediaIndex) {
        if (!item.media_count || !item.id) return null;
        const baseUrl = `/api/catalog/${item.id}/media/${mediaIndex}`;
        if (!state.webappSessionToken) return baseUrl;
        const joiner = baseUrl.includes('?') ? '&' : '?';
        return `${baseUrl}${joiner}session=${encodeURIComponent(state.webappSessionToken)}`;
    }

    function formatDate(dateString) {
        if (!dateString) return '';
        const d = new Date(dateString);
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function showProductDetail(product, addToHistory = true, routeMode = null) {
        logger.userInteraction('view_product', { 
            product_id: product.id,
            category: product.category,
            subcategory: product.subcategory
        });
        
        // Save current state to history for back button
        if (addToHistory) {
            pushHistory({
                view: state.view,
                category: state.currentCategory,
                subcategory: state.currentSubcategory,
                page: state.page
            });
        }
        
        // Update state
        state.view = 'product-detail';
        state.currentProduct = product;
        setBrowserRoute(
            buildProductPageUrl(product),
            {
                appView: 'product-detail',
                productId: product.id,
                category: product.category || null,
                subcategory: product.subcategory || null
            },
            routeMode || (addToHistory ? 'push' : 'replace')
        );
        
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

        const stockStatus = product.stock_status || 'active';
        if (stockStatus === 'out_of_stock') {
            const stockNotice = document.createElement('div');
            stockNotice.className = 'stock-notice';
            stockNotice.textContent = 'Sold out for now. Check similar products or ask about restock.';
            detailContent.appendChild(stockNotice);
        }

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
        let mediaRenderToken = 0;
        
        // Function to render media at index in main viewer
        const renderMedia = (index) => {
            const renderToken = ++mediaRenderToken;
            teardownMediaViewer(mediaViewer);
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
                        url: safeLogUrl(mediaUrl)
                    });
                    
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
                    
                    // Set video src directly so the browser can stream via HTTP Range requests.
                    // This avoids downloading the entire video as a blob before playback,
                    // which was causing slow loading and failures on larger files.
                    video.src = mediaUrl;
                    
                    // Append video to DOM first so the browser can begin buffering
                    // and trigger autoplay. The loading overlay (z-index:5) will cover it.
                    mediaViewer.appendChild(video);
                    
                    // Show loading indicator on top of the video
                    const loadingDiv = document.createElement('div');
                    loadingDiv.className = 'media-loading';
                    loadingDiv.innerHTML = `
                        <div class="media-loading-icon">⏳</div>
                        <div class="media-loading-text">Loading video...</div>
                    `;
                    mediaViewer.appendChild(loadingDiv);
                    
                    // Track if video has been shown to avoid duplicate handling
                    let videoShown = false;
                    const showVideo = () => {
                        if (renderToken !== mediaRenderToken) return;
                        if (videoShown) return;
                        videoShown = true;
                        loadingDiv.style.display = 'none';
                    };
                    
                    // Show video as soon as we have any data to play
                    video.addEventListener('canplay', () => {
                        if (renderToken !== mediaRenderToken) return;
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
                        if (renderToken !== mediaRenderToken) return;
                        showVideo();
                    });
                    
                    // Timeout: if video hasn't loaded after configured time, show play button overlay
                    const videoLoadTimeout = setTimeout(() => {
                        if (renderToken !== mediaRenderToken) return;
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
                        if (renderToken !== mediaRenderToken) return;
                        clearTimeout(videoLoadTimeout);
                        // Check if this is a large-file response without downloading
                        // the entire video again. A plain fetch(mediaUrl) turns one
                        // playback failure into a full duplicate 10-50MB download.
                        try {
                            const response = await fetch(mediaUrl, {
                                headers: { Range: 'bytes=0-0' }
                            });
                            if (renderToken !== mediaRenderToken) return;
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
                        if (renderToken !== mediaRenderToken) return;
                        
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
                        if (renderToken !== mediaRenderToken) return;
                        logger.videoEvent('video_play', 'success', '', {
                            product_id: product.id,
                            media_index: index
                        });
                    });
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
                        if (renderToken !== mediaRenderToken) return;
                        logger.error('image_load_error', 'Failed to load image', {
                            product_id: product.id,
                            media_index: index,
                            url: safeLogUrl(mediaUrl)
                        });
                        mediaViewer.innerHTML = `
                            <div class="media-error">
                                <img class="media-error-brand-icon" src="${CONFIG.SITE_ICON_URL}" alt="Image unavailable">
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
                        <img class="media-error-brand-icon" src="${CONFIG.SITE_ICON_URL}" alt="No product media">
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
                    if (thumbUrl) {
                        const previewVideo = createVideoPreview(
                            thumbUrl,
                            thumbContent,
                            videoPreviewCacheKey(product.id, i)
                        );
                        thumbContent.appendChild(previewVideo);
                    }

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
                            showBrandMediaFallback(thumbContent);
                        };
                        thumbContent.appendChild(thumbImg);
                    } else {
                        showBrandMediaFallback(thumbContent, 'No product media');
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
        const dmUrl = getOrderUrl();
        if (stockStatus === 'out_of_stock') {
            const restockNote = document.createElement('div');
            restockNote.className = 'stock-help';
            restockNote.textContent = 'Sold out for now. Message to ask about restock or similar products.';
            actionsContainer.appendChild(restockNote);
        }
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
                    order_contact: state.orderContact,
                    order_link: dmUrl
                });
            });
            actionsContainer.appendChild(dmBtn);
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
        if (state.orderContact) {
            const username = normalizeContactUsername(state.orderContact);
            if (username) return `https://t.me/${username}`;
        }
        return state.orderLink || null;
    }

    function updateBreadcrumb() {
        const breadcrumbEl = document.getElementById('breadcrumb');
        const parts = [];

        parts.push(`<a href="/" class="breadcrumb-link" data-action="home">${t('home')}</a>`);

        if (state.currentCategory) {
            const categoryData = state.categories.find(c => c.name === state.currentCategory);
            const categoryName = categoryData ? categoryData.display_name : state.currentCategory;
            
            if (state.currentSubcategory) {
                const subcatData = categoryData?.subcategories?.find(s => s.name === state.currentSubcategory);
                const subcategoryName = subcatData ? subcatData.display_name : state.currentSubcategory;
                parts.push(`<a href="${buildCategoryPageUrl(state.currentCategory)}" class="breadcrumb-link" data-action="category">${categoryName}</a>`);
                parts.push(`<span>${subcategoryName}</span>`);
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
                        loadProducts(state.currentCategory, null);
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
