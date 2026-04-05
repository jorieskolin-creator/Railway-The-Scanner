


const { useState, useEffect, useRef, useMemo } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const Icons = new Proxy(LucideRaw, {
    get: (target, prop) => {
        if (prop in target) return target[prop];
        return () => null;
    }
});

const { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield, Database, RefreshCw, CheckCircle2, AlertTriangle, Cloud, RotateCcw, Lock, HardDrive, Radio, Cpu, BarChart3, TrendingUp, TrendingDown, Minus, Trash2, Eye, ShieldAlert, Calculator, Zap, Pencil, Save, Check, LogOut, User } = Icons;

const TruthMatrix = (window as any).TruthMatrix;
const ScannerInput = (window as any).ScannerInput;
const CompanyModal = (window as any).CompanyModal;
const ThemeExplorer = (window as any).ThemeExplorer;
const MarketIntelligenceModal = (window as any).MarketIntelligenceModal;
const ObservatoryModal = (window as any).ObservatoryModal;

// DYNAMIC REFERENCE CONFIG
// These names will be highlighted as References in the UI, 
// but their Data (Scores/Summary) will come from the Backend (Live Scan).
const REFERENCE_NAMES = [
    "Tesla",
    "Buurtzorg",
    "Supercell",
    "DNA",
    "Neste",
    "Kone",
    "Rebase Consulting",
    "Oura"
];

const App = () => {
  const [data, setData] = useState([]); // Lightweight Summary Data
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null); 
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCompany, setSelectedCompany] = useState(null); // Full Detailed Data
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [lastSynced, setLastSynced] = useState(null);
  const [sortConfig, setSortConfig] = useState({ key: 'reality_score', direction: 'desc' });
  const [systemStatus, setSystemStatus] = useState({ online: false, storage: false, google: false, ai: false });
  const [selectedDomain, setSelectedDomain] = useState('All');
  
  // ANALYTICS STATE
  const [isAnalyticsOpen, setIsAnalyticsOpen] = useState(false);
  const [analyticsData, setAnalyticsData] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsMatrixType, setAnalyticsMatrixType] = useState('spearman'); // 'spearman' | 'pearson'
  const [analyticsTab, setAnalyticsTab] = useState('reality_financial'); // 'reality_financial' | 'intent_reality' | 'intent_financial'

  // MARKET INTELLIGENCE STATE
  const [isMarketIntelOpen, setIsMarketIntelOpen] = useState(false);
  const [marketIntelData, setMarketIntelData] = useState(null);
  const [marketIntelLoading, setMarketIntelLoading] = useState(false);

  // OBSERVATORY STATE
  const [isObservatoryOpen, setIsObservatoryOpen] = useState(false);
  const [observatoryData, setObservatoryData] = useState(null);
  const [observatoryLoading, setObservatoryLoading] = useState(false);

  // THEME EXPLORER STATE
  const [isThemeExplorerOpen, setIsThemeExplorerOpen] = useState(false);

  // AUTH STATE — session-based (cleared on tab close)
  const [userRole, setUserRole] = useState(() => sessionStorage.getItem('scanner_role') || 'none');
  const [authToken, setAuthToken] = useState(() => sessionStorage.getItem('scanner_token') || '');
  const [isLoginOpen, setIsLoginOpen] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  const isAuthenticated = userRole === 'guest' || userRole === 'admin';
  const isAdmin = userRole === 'admin';

  const authHeaders = () => authToken ? { 'Authorization': `Bearer ${authToken}` } : {};

  const handleLogin = async (password: string) => {
    setLoginLoading(true);
    setLoginError('');
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      if (res.status === 429) {
        const json = await res.json();
        setLoginError(json.detail || 'Too many attempts. Try again later.');
        return;
      }
      if (!res.ok) {
        const json = await res.json();
        setLoginError(json.detail || 'Invalid password.');
        return;
      }
      const { role } = await res.json();
      setUserRole(role);
      setAuthToken(password);
      sessionStorage.setItem('scanner_role', role);
      sessionStorage.setItem('scanner_token', password);
      setIsLoginOpen(false);
      setLoginError('');
    } catch (e) {
      setLoginError('Connection failed. Try again.');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLogout = () => {
    setUserRole('none');
    setAuthToken('');
    sessionStorage.removeItem('scanner_role');
    sessionStorage.removeItem('scanner_token');
    // Close any open authenticated modals
    setIsAnalyticsOpen(false);
    setIsMarketIntelOpen(false);
    setIsObservatoryOpen(false);
    setIsThemeExplorerOpen(false);
    setIsDataReviewOpen(false);
  };

  // SERVER-SIDE BATCH STATE
  const [batchStatus, setBatchStatus] = useState({ is_running: false, current: 0, total: 0, current_name: '' });

  // DATA REVIEW STATE
  const [isDataReviewOpen, setIsDataReviewOpen] = useState(false);
  const [dataReviewItems, setDataReviewItems] = useState([]);
  const [dataReviewLoading, setDataReviewLoading] = useState(false);
  const [recheckLoading, setRecheckLoading] = useState(false);
  const [cleaningCompany, setCleaningCompany] = useState(null);
  const [recheckResults, setRecheckResults] = useState(null);
  const [recalcLoading, setRecalcLoading] = useState(false);
  const [recalcResults, setRecalcResults] = useState(null);
  const [refreshProposalsLoading, setRefreshProposalsLoading] = useState(false);
  const [refreshProposalsResults, setRefreshProposalsResults] = useState(null);
  const [refreshingProposalsCompany, setRefreshingProposalsCompany] = useState(null);
  const [enrichStakeholdersLoading, setEnrichStakeholdersLoading] = useState(false);
  const [enrichStakeholdersResults, setEnrichStakeholdersResults] = useState(null);
  const [enrichingStakeholderCompany, setEnrichingStakeholderCompany] = useState(null);
  const [foresightKbLoading, setForesightKbLoading] = useState(false);
  const [foresightKbStatus, setForesightKbStatus] = useState(null);
  const [kbCleanupLoading, setKbCleanupLoading] = useState(false);

  // MANUAL EDIT STATE
  const [editingCompany, setEditingCompany] = useState(null);  // company name being edited
  const [editData, setEditData] = useState({ summary_text: '', pitch_text: '', general_summary: '' });
  const [editLoading, setEditLoading] = useState(false);
  const [editSaving, setEditSaving] = useState(false);

  const prevDataLengthRef = useRef(0);

  const checkHealth = async () => {
      try {
          const res = await fetch('/api/health');
          if (res.ok) {
              const json = await res.json();
              setSystemStatus({
                  online: true,
                  storage: json.storage_connected || false,
                  google: true, // Assumed if online
                  ai: true
              });
              return json.storage_connected;
          }
      } catch (e) {
          console.warn("Backend health check failed", e);
          setSystemStatus(prev => ({ ...prev, online: false }));
      }
      return false;
  };

  useEffect(() => {
    checkHealth();
    // Check if a batch refresh is already running on the server
    fetch(`/batch-status?t=${Date.now()}`).then(r => r.ok ? r.json() : null).then(status => {
      if (status && status.is_running) setBatchStatus(status);
    }).catch(() => {});
  }, []);

  const fetchSummaryData = async (silent = false) => {
    try {
        if (!silent) setLoading(true);
        setConnectionError(null);
        const response = await fetch(`/dashboard-data?t=${Date.now()}`);
        if (response.ok) {
            const json = await response.json();
            const newData = json || [];
            prevDataLengthRef.current = newData.length;
            
            // PROCESS REFERENCES: Flag them if they exist in the live data
            const processed = newData.map(item => {
                const isRef = REFERENCE_NAMES.some(refName => 
                    item.companyName.toLowerCase().includes(refName.toLowerCase())
                );
                return isRef ? { ...item, isReference: true } : item;
            });

            setData(processed);
            setAnalyticsData(null); // Invalidate analytics cache so stats modal fetches fresh data
            setLastSynced(new Date().toLocaleTimeString('en-US', { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
        } else {
            throw new Error("Failed to fetch summary data");
        }
    } catch (e) {
        console.error(e);
        if (!silent) setData([]); 
    } finally {
        if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummaryData();
  }, []);

  // Poll batch status when a refresh is running
  useEffect(() => {
    if (!batchStatus.is_running) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/batch-status?t=${Date.now()}`);
        if (res.ok) {
          const status = await res.json();
          setBatchStatus(status);
          // When batch finishes, refresh dashboard data
          if (!status.is_running) {
            fetchSummaryData(true);
          }
        }
      } catch (e) {
        console.warn("Batch status poll failed", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [batchStatus.is_running]);


  const handleCompanyClick = async (summaryItem) => {
    if (!summaryItem) return;
    
    setIsLoadingDetails(true);
    setSelectedCompany({ ...summaryItem, partial: true });
    setIsModalOpen(true);

    // Lazy-load Market Intelligence (for FAVI data) in background if not yet fetched
    if (!marketIntelData && !marketIntelLoading) {
      fetchMarketIntelData();
    }
    // Lazy-load Observatory (for domain dossier) in background if not yet fetched
    if (!observatoryData && !observatoryLoading) {
      fetchObservatoryData();
    }

    try {
        let fullData = null;
        if (summaryItem.detailsUrl) {
            const res = await fetch(`${summaryItem.detailsUrl}?t=${Date.now()}`);
            if (res.ok) fullData = await res.json();
        }
        
        if (!fullData) {
            const res = await fetch(`/company/${encodeURIComponent(summaryItem.companyName)}?t=${Date.now()}`);
            if (res.ok) fullData = await res.json();
        }

        if (fullData) {
            // Apply Reference Flag to details as well to keep UI consistent (e.g. if we add badges later)
            const isRef = REFERENCE_NAMES.some(refName => 
                fullData.companyName.toLowerCase().includes(refName.toLowerCase())
            );
            setSelectedCompany({ ...fullData, isReference: isRef });
        } else {
            setSelectedCompany(prev => ({ ...prev, partial: false, summary_text: "Detailed forensic report unavailable. Displaying summary index data." }));
        }
    } catch (e) {
        console.error("Error fetching details", e);
        setSelectedCompany(prev => ({ ...prev, partial: false }));
    } finally {
        setIsLoadingDetails(false);
    }
  };

  const handleRunScan = async (input, isFormData = false) => {
    if (!isAdmin) return;

    setIsScanning(true);
    try {
      let body;
      let headers: Record<string, string> = { ...authHeaders() };

      if (isFormData) {
          body = input;
      } else {
          body = JSON.stringify({ ...input, saveToDatabase: true });
          headers['Content-Type'] = 'application/json';
      }

      const response = await fetch(`/scan`, { method: 'POST', headers: headers, body: body });
      if (response.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (!response.ok) throw new Error("Scan failed");
      const json = await response.json();
      const newResult = json.data;

      setIsScannerOpen(false);
      setSelectedCompany(newResult);
      setIsModalOpen(true);
      fetchSummaryData(true);
      // Refresh Market Intelligence (FAVI data) after new scan
      fetchMarketIntelData();
      
    } catch (error) {
      console.error(error);
      alert("Error running scan.");
    } finally {
      setIsScanning(false);
    }
  };

  // --- SERVER-SIDE BATCH LOGIC ---
  const handleStartBatch = async () => {
    if (!isAdmin) return;

    if (!confirm("Start full database refresh?\n\nThis runs on the server — you can close this tab safely.\nProgress will be shown in the header.")) return;

    try {
        const res = await fetch('/batch-refresh', {
            method: 'POST',
            headers: authHeaders()
        });
        if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
        if (res.status === 409) { alert("A batch refresh is already in progress."); return; }
        if (!res.ok) throw new Error("Failed to start batch");
        const json = await res.json();
        setBatchStatus({ is_running: true, current: 0, total: json.total, current_name: 'Starting...' });
    } catch(e) { alert("Failed to start batch refresh."); console.error(e); }
  };

  const handleStopBatch = async () => {
    if (!isAdmin) return;
    try {
        await fetch('/batch-refresh/stop', {
            method: 'POST',
            headers: authHeaders()
        });
    } catch(e) { console.error(e); }
  };

  const handleOpenAnalytics = async () => {
    setIsAnalyticsOpen(true);
    setAnalyticsLoading(true);
    try {
      const res = await fetch(`/api/analyze?t=${Date.now()}`);
      if (res.ok) {
        const result = await res.json();
        setAnalyticsData(result);
      }
    } catch (e) {
      console.error('Analytics fetch failed:', e);
    } finally {
      setAnalyticsLoading(false);
    }
  };

  const fetchMarketIntelData = async () => {
    setMarketIntelLoading(true);
    try {
      const res = await fetch(`/api/market-intelligence?t=${Date.now()}`);
      if (res.ok) {
        const result = await res.json();
        setMarketIntelData(result);
      }
    } catch (e) {
      console.error('Market intelligence fetch failed:', e);
    } finally {
      setMarketIntelLoading(false);
    }
  };

  const handleOpenMarketIntel = async () => {
    if (!isAuthenticated) return;
    setIsMarketIntelOpen(true);
    if (!marketIntelData) {
      await fetchMarketIntelData();
    }
    // Lazy-load Observatory data for Finnish Economy Force Map in Future Demand tab
    if (!observatoryData && !observatoryLoading) {
      fetchObservatoryData();
    }
  };

  const fetchObservatoryData = async () => {
    setObservatoryLoading(true);
    try {
      const res = await fetch(`/api/observatory?t=${Date.now()}`);
      if (res.ok) {
        const result = await res.json();
        setObservatoryData(result);
      }
    } catch (e) {
      console.error('Observatory fetch failed:', e);
    } finally {
      setObservatoryLoading(false);
    }
  };

  const handleOpenObservatory = async () => {
    if (!isAuthenticated) return;
    setIsObservatoryOpen(true);
    if (!observatoryData) {
      await fetchObservatoryData();
    }
    // Lazy-load Market Intelligence data for charts in Observatory
    if (!marketIntelData && !marketIntelLoading) {
      fetchMarketIntelData();
    }
  };

  const handleOpenDataReview = async () => {
    if (!isAdmin) return;
    setIsDataReviewOpen(true);
    setDataReviewLoading(true);
    setRecheckResults(null);
    try {
      const res = await fetch('/admin/review-data', {
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        const json = await res.json();
        setDataReviewItems(json.companies || []);
      }
    } catch (e) { console.error(e); alert("Failed to load review data."); }
    finally { setDataReviewLoading(false); }
  };

  const handleDeleteCompany = async (companyName) => {
    if (!isAdmin) return;
    if (!confirm(`DELETE all data for "${companyName}"?\n\nThis removes the company from storage and the dashboard index.\nYou can rescan it later to regenerate fresh data.`)) return;

    try {
      const res = await fetch(`/admin/company/${encodeURIComponent(companyName)}`, {
        method: 'DELETE',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        setDataReviewItems(prev => prev.filter(c => c.companyName !== companyName));
        fetchSummaryData(true);
      } else {
        alert("Failed to delete company.");
      }
    } catch (e) { console.error(e); alert("Delete failed."); }
  };

  const handleCleanupIndex = async () => {
    if (!isAdmin) return;
    try {
      const res = await fetch('/admin/cleanup-index', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        const json = await res.json();
        if (json.removed_count > 0) {
          alert(`Removed ${json.removed_count} orphaned entries:\n${json.removed.join('\n')}`);
          fetchSummaryData(true);
        } else {
          alert("No orphaned entries found. Index is clean.");
        }
      }
    } catch (e) { console.error(e); alert("Cleanup failed."); }
  };

  const handleCleanCompany = async (companyName) => {
    if (!isAdmin) return;
    setCleaningCompany(companyName);
    try {
      const res = await fetch(`/admin/recheck-company/${encodeURIComponent(companyName)}`, {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        const json = await res.json();
        const rec = json.recommendation || '';
        if (json.clean_action === 'cleaned') {
          alert(`Reality Check + Clean "${companyName}":\n\n${json.claims_removed} false claim(s) removed.\nRe-verified: ${json.re_verified ? 'PASSED' : 'Still has issues'}\n\nRecommendation: ${rec}`);
        } else if (json.clean_action === 'no_change') {
          alert(`Reality Check "${companyName}":\n\nCleaning could not resolve false claims — summary unchanged.\n\nRecommendation: ${rec}`);
        } else if (json.clean_action === 'failed') {
          alert(`Reality Check "${companyName}":\n\nCleaning failed.\n\nRecommendation: ${rec}`);
        } else if (rec === 'OK') {
          alert(`Reality Check "${companyName}":\n\nAll checks passed — no cleaning needed.`);
        } else {
          alert(`Reality Check "${companyName}":\n\nRecommendation: ${rec}`);
        }
        // Refresh the review list to show updated verification status
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Reality Check + Clean request failed.");
      }
    } catch (e) { console.error(e); alert("Reality Check + Clean failed."); }
    finally { setCleaningCompany(null); }
  };

  const handleRecheckAll = async () => {
    if (!isAdmin) return;
    setRecheckLoading(true);
    try {
      const res = await fetch('/admin/recheck-all', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setRecheckLoading(false); return; }
      if (res.ok) {
        const json = await res.json();
        setRecheckResults(json);
        // Refresh review list since recheck-all now auto-cleans flagged companies
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      }
    } catch (e) { console.error(e); alert("Recheck failed."); }
    finally { setRecheckLoading(false); }
  };

  const handleRecalculateFinancial = async () => {
    if (!isAdmin) return;
    setRecalcLoading(true);
    setRecalcResults(null);
    try {
      const res = await fetch('/admin/recalculate-financial', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setRecalcLoading(false); return; }
      if (res.ok) {
        const json = await res.json();
        setRecalcResults(json);
        // Refresh review list and dashboard
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Recalculation failed: " + (await res.text()));
      }
    } catch (e) { console.error(e); alert("Recalculation failed."); }
    finally { setRecalcLoading(false); }
  };

  const handleRefreshProposalsAll = async () => {
    if (!isAdmin) return;
    setRefreshProposalsLoading(true);
    setRefreshProposalsResults(null);
    try {
      const res = await fetch('/admin/refresh-proposals-all', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setRefreshProposalsLoading(false); return; }
      if (res.ok) {
        const json = await res.json();
        setRefreshProposalsResults(json);
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Refresh proposals failed: " + (await res.text()));
      }
    } catch (e) { console.error(e); alert("Refresh proposals failed."); }
    finally { setRefreshProposalsLoading(false); }
  };

  const handleRefreshProposalsSingle = async (companyName) => {
    if (!isAdmin) return;
    setRefreshingProposalsCompany(companyName);
    try {
      const res = await fetch(`/admin/refresh-proposals/${encodeURIComponent(companyName)}`, {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        const json = await res.json();
        if (json.status === 'refreshed') {
          alert(`WHY/WHAT Proposals refreshed for "${companyName}" (${json.model_used}).\n\nWHY 1: ${json.new_why[0]}\nWHY 2: ${json.new_why[1]}\n\nWHAT 1: ${json.new_what[0]}\nWHAT 2: ${json.new_what[1]}`);
        } else {
          alert(`Refresh failed for "${companyName}": ${json.reason || 'Unknown error'}`);
        }
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Refresh proposals failed: " + (await res.text()));
      }
    } catch (e) { console.error(e); alert("Refresh proposals failed."); }
    finally { setRefreshingProposalsCompany(null); }
  };

  const handleEnrichStakeholdersAll = async () => {
    if (!isAdmin) return;
    setEnrichStakeholdersLoading(true);
    setEnrichStakeholdersResults(null);
    try {
      const res = await fetch('/admin/enrich-stakeholders-all', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setEnrichStakeholdersLoading(false); return; }
      if (res.ok) {
        const json = await res.json();
        setEnrichStakeholdersResults(json);
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Enrich stakeholders failed: " + (await res.text()));
      }
    } catch (e) { console.error(e); alert("Enrich stakeholders failed."); }
    finally { setEnrichStakeholdersLoading(false); }
  };

  const handleEnrichStakeholdersSingle = async (companyName) => {
    if (!isAdmin) return;
    setEnrichingStakeholderCompany(companyName);
    try {
      const res = await fetch(`/admin/enrich-stakeholders/${encodeURIComponent(companyName)}`, {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); return; }
      if (res.ok) {
        const json = await res.json();
        if (json.status === 'enriched') {
          const details = json.people.map(p => `${p.found ? '✅' : '⚪'} ${p.name} (${p.role}): ${p.summary}`).join('\n\n');
          alert(`Stakeholder Intel for "${companyName}"\n${json.found_insights}/${json.total_people} with public insights (${json.duration_ms}ms)\n\n${details}`);
        } else {
          alert(`Enrichment skipped for "${companyName}": ${json.reason || 'Unknown'}`);
        }
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        fetchSummaryData(true);
      } else {
        alert("Enrich stakeholders failed: " + (await res.text()));
      }
    } catch (e) { console.error(e); alert("Enrich stakeholders failed."); }
    finally { setEnrichingStakeholderCompany(null); }
  };

  const handleRefreshForesightKb = async () => {
    if (!isAdmin) return;
    setForesightKbLoading(true);
    setForesightKbStatus(null);
    try {
      const res = await fetch('/admin/refresh-foresight-kb', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setForesightKbLoading(false); return; }
      if (res.status === 409) { alert("Foresight KB harvest already in progress."); setForesightKbLoading(false); return; }
      if (res.ok) {
        alert("Foresight KB harvest started! Harvesting 30 themes in background.\nCheck status with the status indicator.");
        // Poll for completion
        const pollInterval = setInterval(async () => {
          try {
            const statusRes = await fetch('/admin/foresight-kb-status');
            if (statusRes.ok) {
              const status = await statusRes.json();
              setForesightKbStatus(status);
              if (!status.harvest.is_running && status.harvest.completed_at) {
                clearInterval(pollInterval);
                setForesightKbLoading(false);
                const r = status.harvest.result;
                if (r && !r.error) {
                  alert(`Foresight KB Harvest Complete!\n\n${r.harvested}/${r.harvested + r.failed} themes harvested\n${r.total_signals} signals stored`);
                } else if (r && r.error) {
                  alert(`Foresight KB Harvest Failed: ${r.error}`);
                }
              }
            }
          } catch (e) { /* ignore poll errors */ }
        }, 10000);
      } else {
        alert("Foresight KB refresh failed: " + (await res.text()));
        setForesightKbLoading(false);
      }
    } catch (e) { console.error(e); alert("Foresight KB refresh failed."); setForesightKbLoading(false); }
  };

  const handleCleanupForesightKb = async () => {
    if (!isAdmin) return;
    setKbCleanupLoading(true);
    try {
      const res = await fetch('/admin/cleanup-foresight-kb', {
        method: 'POST',
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setKbCleanupLoading(false); return; }
      if (res.status === 409) { alert((await res.json()).detail); setKbCleanupLoading(false); return; }
      if (res.ok) {
        alert("KB cleanup started! Re-verifying all stored URLs in background.");
        const pollInterval = setInterval(async () => {
          try {
            const statusRes = await fetch('/admin/foresight-kb-cleanup-status');
            if (statusRes.ok) {
              const status = await statusRes.json();
              if (!status.is_running && status.completed_at) {
                clearInterval(pollInterval);
                setKbCleanupLoading(false);
                const r = status.result;
                if (r && !r.error) {
                  alert(`KB Cleanup Complete!\n\n${r.signals_removed} dead signals removed\n${r.signals_after} signals remaining across ${r.themes_checked} themes`);
                } else if (r && r.error) {
                  alert(`KB Cleanup Failed: ${r.error}`);
                }
              }
            }
          } catch (e) { /* ignore poll errors */ }
        }, 8000);
      } else {
        alert("KB cleanup failed: " + (await res.text()));
        setKbCleanupLoading(false);
      }
    } catch (e) { console.error(e); alert("KB cleanup failed."); setKbCleanupLoading(false); }
  };

  // MANUAL EDIT HANDLERS
  const handleOpenEdit = async (companyName) => {
    if (!isAdmin) return;
    setEditLoading(true);
    setEditingCompany(companyName);
    try {
      const res = await fetch(`/admin/get-company/${encodeURIComponent(companyName)}`, {
        headers: authHeaders()
      });
      if (res.status === 401) { alert("Session expired. Please log in again."); handleLogout(); setEditingCompany(null); return; }
      if (!res.ok) { alert("Failed to load company data."); setEditingCompany(null); return; }
      const data = await res.json();
      setEditData({
        summary_text: data.summary_text || '',
        pitch_text: data.pitch_text || '',
        general_summary: data.general_summary || '',
      });
    } catch (e) { console.error(e); alert("Failed to load company data."); setEditingCompany(null); }
    finally { setEditLoading(false); }
  };

  const handleSaveEdit = async () => {
    if (!editingCompany || !isAdmin) return;
    setEditSaving(true);
    try {
      const res = await fetch(`/admin/save-company-edit/${encodeURIComponent(editingCompany)}`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          summary_text: editData.summary_text,
          pitch_text: editData.pitch_text,
          general_summary: editData.general_summary,
        }),
      });
      if (res.status === 401) { alert("ACCESS DENIED"); return; }
      if (!res.ok) { alert("Save failed: " + (await res.text())); return; }
      const json = await res.json();
      if (json.status === 'no_change') {
        alert("No changes detected.");
      } else {
        alert(`Saved: ${json.changed_fields.join(', ')}`);
        // Refresh review list and dashboard
        const reviewRes = await fetch('/admin/review-data', { headers: authHeaders() });
        if (reviewRes.ok) { const reviewJson = await reviewRes.json(); setDataReviewItems(reviewJson.companies || []); }
        // Refresh company modal with saved data so edits appear immediately
        if (selectedCompany) {
          const updated = { ...selectedCompany };
          for (const field of json.changed_fields) {
            if (field in editData) updated[field] = editData[field];
          }
          setSelectedCompany(updated);
        }
        fetchSummaryData(true);
      }
      setEditingCompany(null);
    } catch (e) { console.error(e); alert("Save failed."); }
    finally { setEditSaving(false); }
  };

  const handleSort = (key) => {
    let direction = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') direction = 'asc';
    setSortConfig({ key, direction });
  };

  // Revenue tier helper
  const getRevenueTier = (revenue) => {
    if (!revenue) return { label: '-', size: 'S' };
    if (revenue >= 2000) return { label: 'XL', size: 'XL' };
    if (revenue >= 500) return { label: 'L', size: 'L' };
    if (revenue >= 100) return { label: 'M', size: 'M' };
    return { label: 'S', size: 'S' };
  };

  // Archetype styles and helpers (must be defined before useMemo hooks that reference them)
  const ARCHETYPE_STYLES = {
      "Thriving": { color: "text-emerald-400", bg: "bg-emerald-400/10", border: "border-emerald-400/20" },
      "Modernizing": { color: "text-cyan-400", bg: "bg-cyan-400/10", border: "border-cyan-400/20" },
      "Cash Cow": { color: "text-amber-400", bg: "bg-amber-400/10", border: "border-amber-400/20" },
      "At Risk": { color: "text-rose-400", bg: "bg-rose-400/10", border: "border-rose-400/20" },
  };

  const getArchetypeFromScores = (reality: number, financial: number) => {
      if (reality >= 50) {
          return financial >= 50 ? "Thriving" : "Modernizing";
      } else {
          return financial >= 50 ? "Cash Cow" : "At Risk";
      }
  };

  const getArchetype = (storedArchetype: string | null, reality: number, financial: number) => {
      const label = (storedArchetype && ARCHETYPE_STYLES[storedArchetype])
          ? storedArchetype
          : getArchetypeFromScores(reality, financial);
      return { label, ...ARCHETYPE_STYLES[label] };
  };

  // Collect unique domains from data
  const availableDomains = useMemo(() => {
    const domains = new Set();
    data.forEach(item => { if (item.domain) domains.add(item.domain); });
    return ['All', ...Array.from(domains).sort()];
  }, [data]);

  // Domain-level stats for gauge cards
  const domainStats = useMemo(() => {
    const items = selectedDomain === 'All' ? data : data.filter(d => d.domain === selectedDomain);
    if (items.length === 0) return null;
    const avgIntent = Math.round(items.reduce((s, i) => s + (i.intent_score || 0), 0) / items.length);
    const avgReality = Math.round(items.reduce((s, i) => s + (i.reality_score || 0), 0) / items.length);
    const financialItems = items.filter(i => i.financial_index != null);
    const avgFinancial = financialItems.length > 0 ? Math.round(financialItems.reduce((s, i) => s + i.financial_index, 0) / financialItems.length) : null;
    let archetypes = { 'Thriving': 0, 'Modernizing': 0, 'Cash Cow': 0, 'At Risk': 0 };
    items.forEach(i => {
      const label = getArchetype(i.archetype, i.reality_score || 0, i.financial_index ?? i.intent_score ?? 0).label;
      archetypes[label]++;
    });
    return { count: items.length, avgIntent, avgReality, avgFinancial, archetypes };
  }, [data, selectedDomain]);

  const filteredData = data.filter(item => {
    const matchesSearch = item.companyName.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesDomain = selectedDomain === 'All' || item.domain === selectedDomain;
    return matchesSearch && matchesDomain;
  });

  const sortedData = [...filteredData].sort((a, b) => {
    if (!sortConfig) return 0;
    if (sortConfig.key === 'archetype') {
         const archA = getArchetype(a.archetype, a.reality_score, a.financial_index ?? a.intent_score ?? 0).label;
         const archB = getArchetype(b.archetype, b.reality_score, b.financial_index ?? b.intent_score ?? 0).label;
         return sortConfig.direction === 'asc' ? archA.localeCompare(archB) : archB.localeCompare(archA);
    }
    const aVal = a[sortConfig.key];
    const bVal = b[sortConfig.key];
    const aNull = aVal == null;
    const bNull = bVal == null;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    if (typeof aVal === 'string' && typeof bVal === 'string') return sortConfig.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    if (typeof aVal === 'number' && typeof bVal === 'number') return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
    return 0;
  });

  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-x-hidden text-slate-100">
      <div className="fixed inset-0 z-0 bg-[#1e1b4b] overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#2e1065] via-[#1e1b4b] to-[#0f172a] opacity-80"></div>
        <div className="absolute inset-0 opacity-[0.05] pointer-events-none"></div>
      </div>

      <header className="sticky top-0 z-40 w-full border-b border-white/10 bg-[#1e1b4b]/50 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-cyan-500 to-violet-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">FI</span>
            </div>
            <div className="flex flex-col">
              <h1 className="font-bold text-base text-white hidden sm:block">
                Adaptability<span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 to-violet-300">Scanner</span>
              </h1>
              <span className="text-[10px] text-indigo-200 tracking-[0.15em] uppercase font-medium">Finland Top 100 Index</span>
            </div>
            {/* Auth indicator */}
            {!isAuthenticated ? (
              <button onClick={() => setIsLoginOpen(true)} className="flex items-center gap-1.5 px-2.5 py-1 bg-white/5 hover:bg-white/10 border border-white/10 rounded-md transition-all ml-2">
                <Lock size={11} className="text-slate-400" />
                <span className="text-[9px] font-mono uppercase text-slate-400">Login</span>
              </button>
            ) : (
              <div className="relative group ml-2">
                <button className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border transition-all ${isAdmin ? 'bg-amber-900/30 border-amber-700/50 text-amber-300' : 'bg-teal-900/30 border-teal-700/50 text-teal-300'}`}>
                  {isAdmin ? <Shield size={11} /> : <Eye size={11} />}
                  <span className="text-[9px] font-mono uppercase">{userRole}</span>
                </button>
                <div className="absolute top-full left-0 mt-1 hidden group-hover:block z-50">
                  <button onClick={handleLogout} className="flex items-center gap-1.5 px-3 py-1.5 bg-[#0f172a] border border-white/10 rounded-md text-[10px] text-rose-400 hover:text-rose-300 font-mono whitespace-nowrap">
                    <LogOut size={10} /> Logout
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-4 w-full max-w-md ml-4 justify-end">
            <div className="flex items-center gap-3 mr-2">
                 <div className="hidden sm:flex items-center gap-1.5">
                     <div className={`w-1.5 h-1.5 rounded-full ${systemStatus.storage ? 'bg-cyan-400' : 'bg-rose-500'}`}></div>
                     <span className="text-[9px] font-mono uppercase text-slate-500">
                        Storage
                     </span>
                 </div>
            </div>
            {/* Guest-accessible buttons (guest + admin) */}
            {isAuthenticated && (
                <>
                <button onClick={handleOpenAnalytics} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-violet-900/40 hover:bg-violet-800/60 border border-violet-700/50 text-violet-200 font-mono font-bold text-[10px] rounded transition-all">
                    <BarChart3 size={12} /> ANALYZE
                </button>
                <button onClick={handleOpenMarketIntel} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-cyan-900/40 hover:bg-cyan-800/60 border border-cyan-700/50 text-cyan-200 font-mono font-bold text-[10px] rounded transition-all">
                    <TrendingUp size={12} /> INTEL
                </button>
                <button onClick={handleOpenObservatory} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-violet-900/40 hover:bg-violet-800/60 border border-violet-700/50 text-violet-200 font-mono font-bold text-[10px] rounded transition-all">
                    <Eye size={12} /> OBSERVATORY
                </button>
                <button onClick={() => setIsThemeExplorerOpen(true)} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-teal-900/40 hover:bg-teal-800/60 border border-teal-700/50 text-teal-200 font-mono font-bold text-[10px] rounded transition-all">
                    <Database size={12} /> THEMES
                </button>
                </>
            )}
            {/* Admin-only buttons */}
            {isAdmin && (
                <>
                <button onClick={handleOpenDataReview} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-amber-900/40 hover:bg-amber-800/60 border border-amber-700/50 text-amber-200 font-mono font-bold text-[10px] rounded transition-all">
                    <ShieldAlert size={12} /> REVIEW
                </button>
                {batchStatus.is_running ? (
                    <div className="flex items-center gap-2 px-3 py-1.5 bg-cyan-900/40 border border-cyan-500/40 rounded-lg">
                        <Loader2 size={14} className="animate-spin text-cyan-400" />
                        <span className="text-cyan-200 font-mono font-bold text-[10px]">
                            REFRESH {batchStatus.current}/{batchStatus.total}
                        </span>
                        <button onClick={handleStopBatch} className="text-rose-400 hover:text-rose-300 text-[10px] font-mono font-bold ml-1">
                            STOP
                        </button>
                    </div>
                ) : (
                    <button onClick={handleStartBatch} className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-lg transition-all">
                        <Database size={14} />
                        REFRESH DB
                    </button>
                )}
                </>
            )}
            <div className="flex items-center gap-2">
                {lastSynced && <span className="text-[10px] text-cyan-300/70 font-mono hidden sm:block">{lastSynced}</span>}
                <button onClick={() => fetchSummaryData(false)} className="p-2 bg-white/5 rounded-full hover:bg-white/10">
                    <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                </button>
            </div>
            <div className="relative w-full max-w-[240px] hidden sm:block">
              <input type="text" placeholder="Search..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="w-full pl-4 pr-4 py-1.5 bg-[#0f172a]/50 border border-indigo-500/30 rounded-lg text-sm text-slate-100" />
            </div>
            {isAdmin && (
              <button onClick={() => setIsScannerOpen(true)} className="flex items-center gap-2 px-4 py-2 bg-white/10 rounded-lg text-xs font-bold">
                <Plus size={14} /> NEW DIAGNOSIS
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Sparkles className="text-cyan-300 w-4 h-4" /> Market Landscape
            </h2>
          </div>
          {loading && data.length === 0 ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex items-center justify-center">
                <Loader2 className="animate-spin text-cyan-400" size={32} />
             </div>
          ) : data.length === 0 ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex flex-col items-center justify-center border border-white/5 text-slate-400 gap-4">
                {systemStatus.storage ? (
                    <Database size={48} className="text-cyan-500/50" />
                ) : (
                    <AlertTriangle size={48} className="text-amber-500/50" />
                )}
                <div className="text-center">
                    <h3 className="text-white font-bold text-lg">{systemStatus.storage ? "Database Ready (Empty)" : "Storage Disconnected"}</h3>
                    <p className="text-sm max-w-md mt-2">
                        {systemStatus.storage 
                           ? "Vercel Blob Storage is connected. Initialize via Admin Batch." 
                           : "Could not connect to Vercel Blob."}
                    </p>
                </div>
             </div>
          ) : (
             <div className="bg-[#0f172a]/60 border border-white/10 rounded-2xl p-1 shadow-2xl backdrop-blur-xl">
                <TruthMatrix data={filteredData} onSelectCompany={handleCompanyClick} />
             </div>
          )}

          {/* --- DOMAIN FILTER TABS --- */}
          {data.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {availableDomains.map(domain => (
                <button
                  key={domain}
                  onClick={() => setSelectedDomain(domain)}
                  className={`px-3 py-1 rounded-full text-[10px] font-mono font-semibold uppercase tracking-wide transition-all border ${
                    selectedDomain === domain
                      ? 'bg-cyan-500/20 text-cyan-200 border-cyan-500/50 shadow-[0_0_8px_rgba(34,211,238,0.15)]'
                      : 'bg-white/5 text-slate-400 border-white/10 hover:bg-white/10 hover:text-slate-200'
                  }`}
                >
                  {domain}
                </button>
              ))}
            </div>
          )}

          {/* --- DOMAIN GAUGE CARDS --- */}
          {domainStats && data.length > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Avg Reality</div>
                <div className="text-2xl font-bold text-cyan-300">{domainStats.avgReality}</div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full mt-2 overflow-hidden">
                  <div className="h-full bg-cyan-500 rounded-full transition-all" style={{ width: `${domainStats.avgReality}%` }}></div>
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Avg Financial</div>
                <div className="text-2xl font-bold text-emerald-300">{domainStats.avgFinancial ?? '—'}</div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full mt-2 overflow-hidden">
                  <div className="h-full bg-emerald-500 rounded-full transition-all" style={{ width: `${domainStats.avgFinancial ?? 0}%` }}></div>
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Companies</div>
                <div className="text-2xl font-bold text-white">{domainStats.count}</div>
                <div className="text-[10px] text-slate-500 mt-2 font-mono">
                  {selectedDomain === 'All' ? 'All domains' : selectedDomain}
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Archetype Split</div>
                <div className="flex gap-1 mt-1">
                  {Object.entries(domainStats.archetypes).map(([arch, count]) => {
                    const total = domainStats.count;
                    const pct = total > 0 ? Math.round(((count as number) / total) * 100) : 0;
                    const colors = {
                      'Thriving': 'bg-emerald-500',
                      'Modernizing': 'bg-cyan-500',
                      'Cash Cow': 'bg-amber-500',
                      'At Risk': 'bg-rose-500'
                    };
                    if (pct === 0) return null;
                    return <div key={arch} className={`h-6 rounded ${colors[arch]}`} style={{ width: `${pct}%`, minWidth: pct > 0 ? '8px' : '0' }} title={`${arch}: ${count} (${pct}%)`}></div>;
                  })}
                </div>
                <div className="flex justify-between text-[8px] text-slate-600 mt-1.5 font-mono">
                  <span className="text-emerald-500">{domainStats.archetypes['Thriving']} Thr</span>
                  <span className="text-cyan-500">{domainStats.archetypes['Modernizing']} Mod</span>
                  <span className="text-amber-500">{domainStats.archetypes['Cash Cow']} CC</span>
                  <span className="text-rose-500">{domainStats.archetypes['At Risk']} AR</span>
                </div>
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4">
          <div className="flex flex-col md:flex-row justify-between md:items-center gap-2">
            <h2 className="text-lg font-bold text-white">Forensic Index</h2>
            <div className="flex gap-3 text-[10px] font-mono text-slate-500">
              <span><span className="text-slate-300 font-bold">S</span> &lt;100M</span>
              <span><span className="text-slate-300 font-bold">M</span> 100-499M</span>
              <span><span className="text-slate-300 font-bold">L</span> 500M-2B</span>
              <span><span className="text-slate-300 font-bold">XL</span> &gt;2B revenue (EUR)</span>
            </div>
          </div>
          <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden min-h-[200px]">
              <table className="w-full text-sm text-left">
                <thead className="text-[10px] text-indigo-200 uppercase bg-black/30 border-b border-white/5">
                  <tr>
                    <th className="px-4 py-4 cursor-pointer" onClick={() => handleSort('companyName')}>Entity</th>
                    <th className="px-3 py-4 cursor-pointer hidden md:table-cell" onClick={() => handleSort('domain')}>Domain</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('intent_score')}>Ambition</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('reality_score')}>Reality</th>
                    <th className="px-3 py-4 cursor-pointer hidden md:table-cell" onClick={() => handleSort('financial_index')}>Financial</th>
                    <th className="px-3 py-4 cursor-pointer hidden lg:table-cell" onClick={() => handleSort('revenue_avg_3yr')}>Size</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('archetype')}>Archetype</th>
                    <th className="px-3 py-4">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-slate-300">
                  {sortedData.map((row) => {
                    const archetype = getArchetype(row.archetype, row.reality_score, row.financial_index ?? row.intent_score ?? 0);
                    const revTier = getRevenueTier(row.revenue_avg_3yr);
                    const lowConfidence = row.confidence_score != null && row.confidence_score < 50;
                    const tierColors = { XL: 'text-violet-300 bg-violet-500/15 border-violet-500/30', L: 'text-blue-300 bg-blue-500/15 border-blue-500/30', M: 'text-slate-300 bg-slate-500/15 border-slate-500/30', S: 'text-slate-500 bg-slate-500/10 border-slate-500/20' };
                    return (
                    <tr key={row.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-4 py-3 font-medium text-white">
                        <div className="flex items-center gap-2">
                          {row.companyName}
                          {row.isReference && <span className="text-[9px] bg-green-500/30 text-green-200 px-1 rounded uppercase font-bold">(Ref)</span>}
                          {row.has_docs && <Cpu size={12} className="text-indigo-400" title="Deep Dive Docs Used" />}
                          {lowConfidence && <span className="text-[9px] bg-amber-500/20 text-amber-300 px-1 rounded font-mono" title={`Confidence: ${row.confidence_score}/100`}>?</span>}
                        </div>
                      </td>
                      <td className="px-3 py-3 hidden md:table-cell">
                        <span className="text-[10px] font-mono text-slate-500 truncate block max-w-[120px]">{row.domain || '-'}</span>
                      </td>
                      <td className="px-3 py-3">{row.intent_score}</td>
                      <td className="px-3 py-3">{row.reality_score}</td>
                      <td className="px-3 py-3 hidden md:table-cell">
                        {row.financial_index != null ? <span className="font-mono">{row.financial_index}</span> : <span className="text-slate-600">-</span>}
                      </td>
                      <td className="px-3 py-3 hidden lg:table-cell">
                        <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${tierColors[revTier.size]}`}>
                          {revTier.label}
                        </span>
                      </td>
                      <td className="px-3 py-3 font-mono text-xs">
                         <span className={`px-2.5 py-1 rounded-md border ${archetype.bg} ${archetype.border} ${archetype.color} font-semibold uppercase tracking-wider text-[10px]`}>
                            {archetype.label}
                         </span>
                      </td>
                      <td className="px-3 py-3">
                        <button onClick={() => handleCompanyClick(row)} className="text-cyan-400 hover:text-cyan-300 text-xs font-medium">View Report</button>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
          </div>
        </section>
      </main>

      {isScannerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
           <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={() => !isScanning && setIsScannerOpen(false)} />
           <div className="relative w-full max-w-2xl bg-[#0f172a] border border-white/10 rounded-2xl shadow-2xl overflow-hidden z-10">
               {isScanning ? (
                   <div className="p-16 flex flex-col items-center justify-center">
                       <Loader2 className="animate-spin text-cyan-400 mb-4" size={32} />
                       <p className="text-white">Scanning...</p>
                   </div>
               ) : (
                    <>
                        <div className="absolute top-2 right-2">
                            <button onClick={() => setIsScannerOpen(false)}><X className="text-white" /></button>
                        </div>
                        <ScannerInput onScan={handleRunScan} />
                    </>
               )}
           </div>
        </div>
      )}

      {/* --- ANALYTICS PANEL --- */}
      {isAnalyticsOpen && (() => {
        const ad = analyticsData;
        const view = ad;
        return (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={() => setIsAnalyticsOpen(false)} />
          <div className="relative w-full max-w-4xl bg-[#0f172a] border border-violet-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[90vh] overflow-hidden">
            <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20 shrink-0">
              <h3 className="font-bold text-white flex items-center gap-2">
                <BarChart3 size={18} className="text-violet-400" />
                Statistical Analysis
              </h3>
              <div className="flex items-center gap-3">
                <button onClick={() => setIsAnalyticsOpen(false)} className="text-slate-400 hover:text-white">
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="overflow-y-auto p-6 space-y-6">
              {analyticsLoading ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <Loader2 className="animate-spin text-violet-400 mb-4" size={32} />
                  <p className="text-slate-400 text-sm">Running analysis...</p>
                </div>
              ) : ad?.error ? (
                <div className="text-center py-16 text-slate-400">{ad.error}</div>
              ) : view ? (
                <>
                  {/* HEADER */}
                  <div className="text-center">
                    <div className="text-[10px] text-violet-400 uppercase tracking-widest font-mono mb-1">
                      All Companies — Non-parametric Analysis
                    </div>
                    <div className="text-2xl font-bold text-white">{view.n} Companies Analyzed</div>
                  </div>

                  {/* DISTRIBUTION DIAGNOSTICS (shown when data is skewed) */}
                  {view.distribution_diagnostics && (
                    <div className="bg-violet-500/5 border border-violet-500/20 rounded-xl p-4">
                      <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-2 flex items-center gap-2">
                        <AlertTriangle size={12} /> Distribution Shape
                      </h4>
                      <div className="space-y-1">
                        {Object.entries(view.distribution_diagnostics).map(([key, diag]: [string, any]) => (
                          <div key={key} className="text-[10px] text-slate-400 font-mono">
                            <span className="text-violet-300 capitalize">{key}</span>: {diag.shape} (skew={diag.skew > 0 ? '+' : ''}{diag.skew})
                            {' — '}<span className="text-slate-500">Spearman/Kendall preferred over Pearson</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* CORRELATION ANALYSIS — TABBED */}
                  <div className="bg-violet-500/5 border border-violet-500/20 rounded-xl p-5">
                    {/* Tab navigation */}
                    <div className="flex items-center gap-1 mb-4 bg-black/30 rounded-lg border border-white/10 p-1 overflow-x-auto">
                      {[
                        { key: 'reality_financial', label: 'Reality vs Financial' },
                        { key: 'intent_reality', label: 'Ambition vs Reality' },
                        { key: 'intent_financial', label: 'Ambition vs Financial' },
                      ].map(({ key, label }) => (
                        <button key={key} onClick={() => setAnalyticsTab(key)} className={`px-3 py-1.5 text-[10px] font-mono font-bold rounded-md transition-all whitespace-nowrap ${analyticsTab === key ? 'bg-violet-500/20 text-violet-300 border border-violet-500/40' : 'text-slate-500 hover:text-slate-300'}`}>
                          {label}
                        </button>
                      ))}
                    </div>

                    {/* Tab content — renders the active correlation */}
                    {(() => {
                      const corrKey = analyticsTab === 'reality_financial' ? 'reality_vs_financial' : analyticsTab === 'intent_reality' ? 'intent_vs_reality' : 'intent_vs_financial';
                      const corrData = view[corrKey];
                      const titles = {
                        reality_financial: { title: 'Reality Score vs Financial Index', xLabel: 'R', yLabel: 'F', xColor: 'text-cyan-400', yColor: 'text-emerald-400' },
                        intent_reality: { title: 'Ambition (Intent) vs Reality Score', xLabel: 'I', yLabel: 'R', xColor: 'text-indigo-400', yColor: 'text-cyan-400' },
                        intent_financial: { title: 'Ambition (Intent) vs Financial Index', xLabel: 'I', yLabel: 'F', xColor: 'text-indigo-400', yColor: 'text-emerald-400' },
                      };
                      const t = titles[analyticsTab];
                      return (
                        <>
                          <h4 className="text-sm font-bold text-violet-300 uppercase tracking-wide mb-3 flex items-center gap-2">
                            <TrendingUp size={14} /> {t.title}
                          </h4>
                          {corrData?.spearman_rho != null ? (
                            <div className="space-y-3">
                              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                                <div className="bg-black/30 rounded-lg p-3 text-center">
                                  <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Paired n</div>
                                  <div className="text-xl font-bold text-white">{corrData.n}</div>
                                </div>
                                <div className="bg-black/30 rounded-lg p-3 text-center border border-violet-500/20">
                                  <div className="text-[9px] text-violet-400 uppercase tracking-wider font-mono">Spearman rho</div>
                                  <div className={`text-xl font-bold ${corrData.spearman_rho > 0.3 ? 'text-emerald-400' : corrData.spearman_rho < -0.3 ? 'text-rose-400' : 'text-amber-400'}`}>
                                    {corrData.spearman_rho > 0 ? '+' : ''}{corrData.spearman_rho}
                                  </div>
                                  <div className="text-[8px] text-violet-400/60 font-mono mt-0.5">PRIMARY</div>
                                </div>
                                <div className="bg-black/30 rounded-lg p-3 text-center">
                                  <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Kendall tau</div>
                                  <div className={`text-xl font-bold ${(corrData.kendall_tau ?? 0) > 0.3 ? 'text-emerald-400' : (corrData.kendall_tau ?? 0) < -0.3 ? 'text-rose-400' : 'text-amber-400'}`}>
                                    {corrData.kendall_tau != null ? ((corrData.kendall_tau > 0 ? '+' : '') + corrData.kendall_tau) : 'n/a'}
                                  </div>
                                </div>
                                <div className="bg-black/30 rounded-lg p-3 text-center">
                                  <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Pearson r</div>
                                  <div className="text-xl font-bold text-slate-400">
                                    {corrData.pearson_r > 0 ? '+' : ''}{corrData.pearson_r}
                                  </div>
                                  <div className="text-[8px] text-slate-600 font-mono mt-0.5">assumes normality</div>
                                </div>
                                <div className="bg-black/30 rounded-lg p-3 text-center">
                                  <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Interpretation</div>
                                  <div className="text-sm font-bold text-violet-300 uppercase">{corrData.interpretation}</div>
                                </div>
                              </div>
                              {corrData.spearman_p != null && (
                                <div className="text-[10px] text-slate-500 font-mono text-center">
                                  Spearman p-value = {corrData.spearman_p} {corrData.spearman_p < 0.05 ? '(statistically significant)' : '(not significant)'}
                                  {corrData.kendall_p != null && (<span> | Kendall p-value = {corrData.kendall_p}</span>)}
                                </div>
                              )}
                              {corrData.divergence_note && (
                                <div className="text-[10px] text-amber-400/80 font-mono text-center bg-amber-500/5 rounded-lg p-2 border border-amber-500/10">
                                  {corrData.divergence_note}
                                </div>
                              )}
                              {/* Regression residuals */}
                              {corrData.residuals?.length > 0 && (
                                <div className="mt-3">
                                  <div className="text-[10px] text-slate-500 uppercase tracking-wider font-mono mb-2">Largest Residuals from Regression</div>
                                  <div className="space-y-1">
                                    {corrData.residuals.map((o: any, i: number) => (
                                      <div key={i} className="flex items-center justify-between text-xs font-mono bg-black/20 rounded px-3 py-1.5">
                                        <span className="text-slate-300 truncate max-w-[200px]">{o.name}</span>
                                        <div className="flex gap-4 text-[10px]">
                                          <span className={t.xColor}>{t.xLabel}:{o.x_val ?? o.reality}</span>
                                          <span className={t.yColor}>{t.yLabel}:{o.y_val ?? o.financial}</span>
                                          <span className={o.residual > 0 ? 'text-emerald-400' : 'text-rose-400'}>
                                            {o.residual > 0 ? '+' : ''}{o.residual}
                                          </span>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="text-slate-500 text-sm">Not enough paired data (need 3+ companies with both scores)</div>
                          )}
                        </>
                      );
                    })()}
                  </div>

                  {/* CORRELATION MATRIX */}
                  {view.correlation_matrix && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <div className="flex items-center justify-between mb-3">
                        <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide">
                          Correlation Matrix ({analyticsMatrixType === 'spearman' ? 'Spearman' : 'Pearson'})
                        </h4>
                        <div className="flex bg-black/40 rounded-lg border border-white/10 p-0.5">
                          <button onClick={() => setAnalyticsMatrixType('spearman')} className={`px-2.5 py-0.5 text-[9px] font-mono font-bold rounded-md transition-all ${analyticsMatrixType === 'spearman' ? 'bg-violet-500/20 text-violet-300 border border-violet-500/40' : 'text-slate-500 hover:text-slate-300'}`}>
                            SPEARMAN
                          </button>
                          <button onClick={() => setAnalyticsMatrixType('pearson')} className={`px-2.5 py-0.5 text-[9px] font-mono font-bold rounded-md transition-all ${analyticsMatrixType === 'pearson' ? 'bg-slate-500/20 text-slate-300 border border-slate-500/40' : 'text-slate-500 hover:text-slate-300'}`}>
                            PEARSON
                          </button>
                        </div>
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500">
                              <th className="text-left py-1 px-2 w-24"></th>
                              {['Intent', 'Reality', 'Financial', 'Confidence'].map(h => (
                                <th key={h} className="text-center py-1 px-2">{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {['intent', 'reality', 'financial', 'confidence'].map((ki) => {
                              const matrixData = analyticsMatrixType === 'spearman' ? view.correlation_matrix : (view.correlation_matrix_pearson || view.correlation_matrix);
                              return (
                              <tr key={ki}>
                                <td className="text-slate-400 py-1 px-2 capitalize">{ki}</td>
                                {['intent', 'reality', 'financial', 'confidence'].map((kj) => {
                                  const v = matrixData[ki]?.[kj];
                                  const isIdentity = ki === kj;
                                  const color = v == null ? 'text-slate-600' : isIdentity ? 'text-slate-500' : v > 0.3 ? 'text-emerald-400' : v < -0.3 ? 'text-rose-400' : 'text-slate-300';
                                  return (
                                    <td key={kj} className={`text-center py-1 px-2 ${color}`}>
                                      {v != null ? (v > 0 ? '+' : '') + v.toFixed(3) : 'n/a'}
                                    </td>
                                  );
                                })}
                              </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      {analyticsMatrixType === 'pearson' && (
                        <div className="text-[9px] text-amber-400/60 font-mono text-center mt-2">
                          Pearson assumes normal distribution — use Spearman for skewed data
                        </div>
                      )}
                    </div>
                  )}

                  {/* DESCRIPTIVE STATS */}
                  {view.descriptive && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Descriptive Statistics</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2"></th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Median</th>
                              <th className="text-center py-1 px-1">IQR</th>
                              <th className="text-center py-1 px-1">Mean</th>
                              <th className="text-center py-1 px-1">Std</th>
                              <th className="text-center py-1 px-1">Range</th>
                              <th className="text-center py-1 px-1">Skew</th>
                              <th className="text-center py-1 px-1">Shape</th>
                            </tr>
                          </thead>
                          <tbody>
                            {[['Intent', 'intent'], ['Reality', 'reality'], ['Financial', 'financial'], ['Confidence', 'confidence'], ['Gap (I-R)', 'gap']].map(([label, key]) => {
                              const d = view.descriptive[key];
                              if (!d) return null;
                              return (
                                <tr key={key} className="border-t border-white/5">
                                  <td className="text-slate-400 py-1.5 px-2">{label}</td>
                                  <td className="text-center text-slate-500 py-1.5 px-1">{d.n}</td>
                                  <td className="text-center text-white py-1.5 px-1 font-bold">{d.median}</td>
                                  <td className="text-center text-slate-300 py-1.5 px-1">{d.q1 != null ? `${d.q1}-${d.q3}` : '-'}</td>
                                  <td className="text-center text-slate-400 py-1.5 px-1">{d.mean}</td>
                                  <td className="text-center text-slate-500 py-1.5 px-1">{d.std}</td>
                                  <td className="text-center text-slate-500 py-1.5 px-1">{d.min}-{d.max}</td>
                                  <td className={`text-center py-1.5 px-1 ${d.skew > 0.5 ? 'text-amber-400' : d.skew < -0.5 ? 'text-cyan-400' : 'text-slate-400'}`}>{d.skew > 0 ? '+' : ''}{d.skew}</td>
                                  <td className={`text-center py-1.5 px-1 text-[9px] ${d.skew > 0.5 ? 'text-amber-400/70' : d.skew < -0.5 ? 'text-cyan-400/70' : 'text-slate-500'}`}>{d.shape || '-'}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* ARCHETYPE DISTRIBUTION */}
                  {view.archetypes && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Archetype Distribution</h4>
                      <div className="space-y-2">
                        {[
                          { key: 'Thriving', color: 'bg-emerald-500', text: 'text-emerald-400' },
                          { key: 'Modernizing', color: 'bg-cyan-500', text: 'text-cyan-400' },
                          { key: 'Cash Cow', color: 'bg-amber-500', text: 'text-amber-400' },
                          { key: 'At Risk', color: 'bg-rose-500', text: 'text-rose-400' },
                        ].map(({ key, color, text }) => {
                          const a = view.archetypes[key];
                          if (!a) return null;
                          return (
                            <div key={key} className="flex items-center gap-3">
                              <span className={`${text} text-xs font-mono w-28 text-right`}>{key}</span>
                              <div className="flex-grow h-5 bg-slate-800 rounded overflow-hidden">
                                <div className={`h-full ${color} rounded transition-all`} style={{ width: `${a.pct}%` }}></div>
                              </div>
                              <span className="text-xs font-mono text-slate-400 w-20">{a.count} ({a.pct}%)</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* DOMAIN BREAKDOWN */}
                  {view.by_domain && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Average Scores by Industry</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2">Domain</th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Intent</th>
                              <th className="text-center py-1 px-1">Reality</th>
                              <th className="text-center py-1 px-1">Financial</th>
                              <th className="text-center py-1 px-1">Gap</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(view.by_domain).map(([domain, s]: [string, any]) => (
                              <tr key={domain} className="border-t border-white/5">
                                <td className="text-slate-300 py-1.5 px-2 max-w-[180px] truncate">{domain}</td>
                                <td className="text-center text-slate-500 py-1.5 px-1">{s.n}</td>
                                <td className="text-center text-indigo-300 py-1.5 px-1">{s.avg_intent}</td>
                                <td className="text-center text-cyan-300 py-1.5 px-1">{s.avg_reality}</td>
                                <td className="text-center text-emerald-300 py-1.5 px-1">{s.avg_financial ?? '-'}</td>
                                <td className={`text-center py-1.5 px-1 ${s.avg_gap > 10 ? 'text-amber-400' : s.avg_gap < -10 ? 'text-cyan-400' : 'text-slate-400'}`}>
                                  {s.avg_gap != null ? (s.avg_gap > 0 ? '+' : '') + s.avg_gap : '-'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* OWNERSHIP BREAKDOWN */}
                  {view.by_ownership && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Average Scores by Ownership</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2">Ownership</th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Intent</th>
                              <th className="text-center py-1 px-1">Reality</th>
                              <th className="text-center py-1 px-1">Financial</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(view.by_ownership).map(([owner, s]: [string, any]) => (
                              <tr key={owner} className="border-t border-white/5">
                                <td className="text-slate-300 py-1.5 px-2">{owner}</td>
                                <td className="text-center text-slate-500 py-1.5 px-1">{s.n}</td>
                                <td className="text-center text-indigo-300 py-1.5 px-1">{s.avg_intent}</td>
                                <td className="text-center text-cyan-300 py-1.5 px-1">{s.avg_reality}</td>
                                <td className="text-center text-emerald-300 py-1.5 px-1">{s.avg_financial ?? '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* TOP 10 / BOTTOM 10 */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {view.top_10_reality && (
                      <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-4">
                        <h4 className="text-[10px] font-bold text-emerald-400 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <TrendingUp size={12} /> Top 10 Reality Score
                        </h4>
                        <div className="space-y-1">
                          {view.top_10_reality.map((c, i) => (
                            <div key={i} className="flex justify-between text-[11px] font-mono py-0.5">
                              <span className="text-slate-300 truncate max-w-[160px]">{i+1}. {c.name}</span>
                              <div className="flex gap-2 text-[10px]">
                                <span className="text-cyan-400">{c.reality}</span>
                                <span className="text-emerald-400">{c.financial ?? '-'}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {view.bottom_10_reality && (
                      <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-4">
                        <h4 className="text-[10px] font-bold text-rose-400 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <TrendingDown size={12} /> Bottom 10 Reality Score
                        </h4>
                        <div className="space-y-1">
                          {view.bottom_10_reality.map((c, i) => (
                            <div key={i} className="flex justify-between text-[11px] font-mono py-0.5">
                              <span className="text-slate-300 truncate max-w-[160px]">{i+1}. {c.name}</span>
                              <div className="flex gap-2 text-[10px]">
                                <span className="text-cyan-400">{c.reality}</span>
                                <span className="text-rose-400">{c.financial ?? '-'}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* DISTRIBUTIONS */}
                  {view.distributions && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Score Distributions</h4>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {[
                          { key: 'reality', label: 'Reality', color: 'bg-cyan-500' },
                          { key: 'financial', label: 'Financial', color: 'bg-emerald-500' },
                          { key: 'intent', label: 'Intent', color: 'bg-indigo-500' },
                        ].map(({ key, label, color }) => {
                          const dist = view.distributions[key];
                          if (!dist) return null;
                          const maxCount = Math.max(...dist.bins.map(b => b.count));
                          return (
                            <div key={key}>
                              <div className="text-[10px] text-slate-500 font-mono uppercase mb-1">{label} (n={dist.n})</div>
                              <div className="space-y-0.5">
                                {dist.bins.map((b, i) => (
                                  <div key={i} className="flex items-center gap-1.5 text-[9px] font-mono">
                                    <span className="text-slate-600 w-12 text-right">{b.range}</span>
                                    <div className="flex-grow h-3 bg-slate-800 rounded overflow-hidden">
                                      <div className={`h-full ${color} rounded`} style={{ width: `${maxCount > 0 ? (b.count / maxCount) * 100 : 0}%` }}></div>
                                    </div>
                                    <span className="text-slate-500 w-4">{b.count}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </div>
        </div>
        );
      })()}

      {/* --- DATA REVIEW PANEL --- */}
      {isDataReviewOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={() => setIsDataReviewOpen(false)} />
          <div className="relative w-full max-w-4xl bg-[#0f172a] border border-amber-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[90vh] overflow-hidden">
            <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20 shrink-0">
              <h3 className="font-bold text-white flex items-center gap-2">
                <ShieldAlert size={18} className="text-amber-400" />
                Data Review & Cleanup
              </h3>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleRecalculateFinancial}
                  disabled={recalcLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-cyan-900/40 hover:bg-cyan-800/60 border border-cyan-600/50 text-cyan-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                >
                  {recalcLoading ? <Loader2 size={12} className="animate-spin" /> : <Calculator size={12} />}
                  RECALC SCORES
                </button>
                <button
                  onClick={handleRefreshProposalsAll}
                  disabled={refreshProposalsLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-orange-900/40 hover:bg-orange-800/60 border border-orange-600/50 text-orange-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                >
                  {refreshProposalsLoading ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                  REFRESH WHY/WHAT
                </button>
                <button
                  onClick={handleEnrichStakeholdersAll}
                  disabled={enrichStakeholdersLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-indigo-900/40 hover:bg-indigo-800/60 border border-indigo-600/50 text-indigo-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                >
                  {enrichStakeholdersLoading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                  STAKEHOLDER INTEL
                </button>
                <button
                  onClick={handleRecheckAll}
                  disabled={recheckLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-amber-900/40 hover:bg-amber-800/60 border border-amber-600/50 text-amber-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                >
                  {recheckLoading ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                  REALITY CHECK ALL
                </button>
                <button
                  onClick={handleRefreshForesightKb}
                  disabled={foresightKbLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-cyan-900/40 hover:bg-cyan-800/60 border border-cyan-600/50 text-cyan-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                  title="Harvest megatrend signals from 30 FP themes into the Foresight Knowledge Base"
                >
                  {foresightKbLoading ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />}
                  {foresightKbLoading && foresightKbStatus?.harvest ? `KB ${foresightKbStatus.harvest.current}/${foresightKbStatus.harvest.total}` : 'REFRESH FORESIGHT KB'}
                </button>
                <button
                  onClick={handleCleanupForesightKb}
                  disabled={kbCleanupLoading || foresightKbLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-teal-900/40 hover:bg-teal-800/60 border border-teal-600/50 text-teal-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                  title="Re-verify all stored KB URLs and remove signals with dead sources"
                >
                  {kbCleanupLoading ? <Loader2 size={12} className="animate-spin" /> : <Shield size={12} />}
                  {kbCleanupLoading ? 'CLEANING KB...' : 'CLEAN KB'}
                </button>
                <button
                  onClick={handleCleanupIndex}
                  className="flex items-center gap-1.5 px-3 py-1 bg-rose-900/40 hover:bg-rose-800/60 border border-rose-600/50 text-rose-200 font-mono font-bold text-[10px] rounded transition-all"
                  title="Remove orphaned entries from the landing page index (companies without data files)"
                >
                  <Trash2 size={12} /> CLEAN INDEX
                </button>
                <button onClick={() => setIsDataReviewOpen(false)} className="text-slate-400 hover:text-white">
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="overflow-y-auto p-4 space-y-4">
              {dataReviewLoading ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <Loader2 className="animate-spin text-amber-400 mb-4" size={32} />
                  <p className="text-slate-400 text-sm">Loading stored data...</p>
                </div>
              ) : (
                <>
                  {/* RECHECK RESULTS BANNER */}
                  {recheckResults && (
                    <div className={`rounded-xl p-4 border ${recheckResults.flagged_count > 0 ? 'bg-rose-500/5 border-rose-500/20' : 'bg-emerald-500/5 border-emerald-500/20'}`}>
                      <div className="flex items-center gap-2 mb-2">
                        {recheckResults.flagged_count > 0 ? (
                          <AlertTriangle size={16} className="text-rose-400" />
                        ) : (
                          <CheckCircle2 size={16} className="text-emerald-400" />
                        )}
                        <span className="font-bold text-white text-sm">
                          Reality Check: {recheckResults.clean} clean, {recheckResults.flagged_count} flagged out of {recheckResults.total_checked}
                        </span>
                      </div>
                      {recheckResults.auto_cleaned > 0 && (
                        <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
                          <CheckCircle2 size={14} className="text-emerald-400" />
                          <span className="text-emerald-300 text-xs font-mono font-bold">
                            Auto-cleaned {recheckResults.auto_cleaned} {recheckResults.auto_cleaned === 1 ? 'company' : 'companies'} — false claims removed and summaries rewritten
                          </span>
                        </div>
                      )}
                      {recheckResults.flagged?.map((item, idx) => (
                        <div key={idx} className={`mt-2 bg-black/30 rounded-lg p-3 border ${item.clean_action === 'cleaned' ? 'border-emerald-500/20' : 'border-rose-500/10'}`}>
                          <div className="flex justify-between items-start">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className={`text-xs font-mono font-bold px-1.5 py-0.5 rounded ${item.risk === 'high' ? 'bg-rose-500/20 text-rose-300' : 'bg-amber-500/20 text-amber-300'}`}>
                                {item.risk.toUpperCase()}
                              </span>
                              <span className="text-white text-sm font-medium">{item.companyName}</span>
                              <span className="text-slate-500 text-[10px] font-mono">score: {item.score}/100</span>
                              {item.clean_action === 'cleaned' && (
                                <span className="text-[9px] bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded font-mono font-bold">
                                  CLEANED ({item.claims_removed} {item.claims_removed === 1 ? 'claim' : 'claims'} removed){item.re_verified ? ' — VERIFIED' : ' — STILL UNVERIFIED'}
                                </span>
                              )}
                              {item.clean_action === 'failed' && (
                                <span className="text-[9px] bg-rose-500/20 text-rose-300 px-1.5 py-0.5 rounded font-mono font-bold">CLEAN FAILED</span>
                              )}
                            </div>
                            <button
                              onClick={() => handleDeleteCompany(item.companyName)}
                              className="text-rose-400 hover:text-rose-300 text-[10px] font-mono font-bold flex items-center gap-1"
                            >
                              <Trash2 size={12} /> DELETE
                            </button>
                          </div>
                          <div className="mt-1.5 space-y-0.5">
                            {item.flags.map((flag, fi) => (
                              <div key={fi} className="text-[10px] text-rose-300/80 font-mono">- {flag}</div>
                            ))}
                          </div>
                          {item.summary_preview && (
                            <div className="mt-2 text-[10px] text-slate-500 font-mono italic truncate">{item.summary_preview}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* RECALC RESULTS BANNER */}
                  {recalcResults && (
                    <div className="rounded-xl p-4 border bg-cyan-500/5 border-cyan-500/20">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <Calculator size={16} className="text-cyan-400" />
                          <span className="font-bold text-white text-sm">
                            Financial Recalculation: {recalcResults.updated} updated, {recalcResults.skipped} skipped out of {recalcResults.total}
                          </span>
                        </div>
                        <button onClick={() => setRecalcResults(null)} className="text-slate-500 hover:text-white"><X size={14} /></button>
                      </div>
                      {recalcResults.changes && recalcResults.changes.length > 0 ? (
                        <div className="space-y-1 max-h-60 overflow-y-auto">
                          {recalcResults.changes.map((c, idx) => (
                            <div key={idx} className="flex items-center gap-3 bg-black/30 rounded-lg px-3 py-2 border border-cyan-500/10">
                              <span className="text-white text-xs font-medium min-w-[140px] truncate">{c.company}</span>
                              <span className="text-slate-500 text-[10px] font-mono">{c.old}</span>
                              <span className="text-slate-600 text-[10px]">&rarr;</span>
                              <span className="text-cyan-300 text-[10px] font-mono font-bold">{c.new}</span>
                              <span className={`text-[10px] font-mono font-bold ${c.delta > 0 ? 'text-emerald-400' : c.delta < 0 ? 'text-rose-400' : 'text-slate-500'}`}>
                                {c.delta > 0 ? '+' : ''}{c.delta}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-cyan-300/60 text-xs font-mono">No scores changed.</p>
                      )}
                    </div>
                  )}

                  {/* REFRESH PROPOSALS RESULTS BANNER */}
                  {refreshProposalsResults && (
                    <div className="rounded-xl p-4 border bg-orange-500/5 border-orange-500/20">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <Zap size={16} className="text-orange-400" />
                          <span className="font-bold text-white text-sm">
                            WHY/WHAT Refresh: {refreshProposalsResults.refreshed} refreshed, {refreshProposalsResults.failed} failed out of {refreshProposalsResults.total}
                          </span>
                        </div>
                        <button onClick={() => setRefreshProposalsResults(null)} className="text-slate-500 hover:text-white"><X size={14} /></button>
                      </div>
                      {refreshProposalsResults.results && refreshProposalsResults.results.length > 0 ? (
                        <div className="space-y-1 max-h-60 overflow-y-auto">
                          {refreshProposalsResults.results.map((r, idx) => (
                            <div key={idx} className="flex items-center gap-3 bg-black/30 rounded-lg px-3 py-2 border border-orange-500/10">
                              <span className="text-white text-xs font-medium min-w-[140px] truncate">{r.company}</span>
                              <span className={`text-[10px] font-mono font-bold ${r.status === 'refreshed' ? 'text-emerald-400' : 'text-rose-400'}`}>
                                {r.status === 'refreshed' ? `✅ ${r.model_used}` : `❌ ${r.reason || 'Failed'}`}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-orange-300/60 text-xs font-mono">No proposals refreshed.</p>
                      )}
                    </div>
                  )}

                  {/* ENRICH STAKEHOLDERS RESULTS BANNER */}
                  {enrichStakeholdersResults && (
                    <div className="rounded-xl p-4 border bg-indigo-500/5 border-indigo-500/20">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <Search size={16} className="text-indigo-400" />
                          <span className="font-bold text-white text-sm">
                            Stakeholder Intel: {enrichStakeholdersResults.enriched} enriched, {enrichStakeholdersResults.skipped} skipped out of {enrichStakeholdersResults.total}
                          </span>
                        </div>
                        <button onClick={() => setEnrichStakeholdersResults(null)} className="text-slate-500 hover:text-white"><X size={14} /></button>
                      </div>
                      {enrichStakeholdersResults.results && enrichStakeholdersResults.results.length > 0 ? (
                        <div className="space-y-1 max-h-60 overflow-y-auto">
                          {enrichStakeholdersResults.results.map((r, idx) => (
                            <div key={idx} className="flex items-center gap-3 bg-black/30 rounded-lg px-3 py-2 border border-indigo-500/10">
                              <span className="text-white text-xs font-medium min-w-[140px] truncate">{r.company}</span>
                              <span className={`text-[10px] font-mono font-bold ${r.status === 'enriched' ? 'text-emerald-400' : r.status === 'skipped' ? 'text-slate-500' : 'text-rose-400'}`}>
                                {r.status === 'enriched' ? `✅ ${r.found}/${r.total} with insights` : r.status === 'skipped' ? '⚪ No stakeholders' : `❌ ${r.reason || 'Failed'}`}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-indigo-300/60 text-xs font-mono">No stakeholders enriched.</p>
                      )}
                    </div>
                  )}

                  {/* STORED COMPANIES LIST */}
                  <div className="text-[10px] text-slate-500 font-mono uppercase tracking-wider">
                    {dataReviewItems.length} companies in database
                  </div>
                  <div className="space-y-1">
                    {dataReviewItems.map((item, idx) => {
                      const hasFlags = item.hallucination_flags && item.hallucination_flags.length > 0;
                      const isHighRisk = item.hallucination_risk === 'high';
                      const isMediumRisk = item.hallucination_risk === 'medium';
                      const isFlaggedSummary = (item.summary_preview || '').includes('[FLAGGED]');
                      const hasUnverifiedClaims = item.ai_verified === false && item.unsupported_claims?.length > 0;
                      return (
                        <div key={idx} className="space-y-1">
                        <div className={`flex items-center justify-between px-3 py-2 rounded-lg border transition-all ${
                          isHighRisk || isFlaggedSummary ? 'bg-rose-500/5 border-rose-500/20' :
                          hasUnverifiedClaims ? 'bg-orange-500/5 border-orange-500/20' :
                          isMediumRisk ? 'bg-amber-500/5 border-amber-500/20' :
                          'bg-black/20 border-white/5 hover:border-white/10'
                        }`}>
                          <div className="flex-grow min-w-0 mr-3">
                            <div className="flex items-center gap-2">
                              <span className="text-sm text-white font-medium truncate">{item.companyName}</span>
                              {item.versions > 1 && (
                                <span className="text-[9px] text-slate-600 font-mono">{item.versions}v</span>
                              )}
                              {isHighRisk && <span className="text-[9px] bg-rose-500/20 text-rose-300 px-1 rounded font-mono font-bold">HIGH RISK</span>}
                              {isMediumRisk && <span className="text-[9px] bg-amber-500/20 text-amber-300 px-1 rounded font-mono font-bold">MEDIUM</span>}
                              {isFlaggedSummary && <span className="text-[9px] bg-rose-500/30 text-rose-200 px-1 rounded font-mono font-bold">BLOCKED</span>}
                              {hasUnverifiedClaims && <span className="text-[9px] bg-orange-500/20 text-orange-300 px-1 rounded font-mono font-bold">UNVERIFIED</span>}
                            </div>
                            <div className="text-[10px] text-slate-500 font-mono mt-0.5 truncate">
                              {item.archetype && <span className="mr-2">{item.archetype}</span>}
                              {item.intent_score != null && <span className="mr-2">I:{item.intent_score}</span>}
                              {item.reality_score != null && <span className="mr-2">R:{item.reality_score}</span>}
                              {item.confidence_score != null && <span className="mr-2">C:{item.confidence_score}</span>}
                              {item.lastUpdated && <span className="text-slate-600">{item.lastUpdated.split('T')[0]}</span>}
                            </div>
                            {hasFlags && (
                              <div className="mt-1 space-y-0.5">
                                {item.hallucination_flags.slice(0, 3).map((flag, fi) => (
                                  <div key={fi} className="text-[9px] text-amber-400/70 font-mono">- {flag}</div>
                                ))}
                              </div>
                            )}
                            {hasUnverifiedClaims && (
                              <div className="mt-1 space-y-0.5">
                                {item.unsupported_claims.slice(0, 3).map((claim, ci) => (
                                  <div key={ci} className="text-[9px] text-orange-400/70 font-mono">- {claim}</div>
                                ))}
                              </div>
                            )}
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => handleEnrichStakeholdersSingle(item.companyName)}
                              disabled={enrichingStakeholderCompany === item.companyName}
                              className="text-slate-500 hover:text-indigo-400 p-1.5 rounded hover:bg-indigo-500/10 transition-all disabled:opacity-50"
                              title={`Enrich stakeholder LinkedIn insights for ${item.companyName}`}
                            >
                              {enrichingStakeholderCompany === item.companyName ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                            </button>
                            <button
                              onClick={() => handleRefreshProposalsSingle(item.companyName)}
                              disabled={refreshingProposalsCompany === item.companyName}
                              className="text-slate-500 hover:text-orange-400 p-1.5 rounded hover:bg-orange-500/10 transition-all disabled:opacity-50"
                              title={`Refresh WHY/WHAT proposals for ${item.companyName}`}
                            >
                              {refreshingProposalsCompany === item.companyName ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                            </button>
                            <button
                              onClick={() => handleCleanCompany(item.companyName)}
                              disabled={cleaningCompany === item.companyName}
                              className={`${hasUnverifiedClaims ? 'text-orange-400' : 'text-slate-500'} hover:text-emerald-400 p-1.5 rounded hover:bg-emerald-500/10 transition-all disabled:opacity-50`}
                              title={`Reality Check${hasUnverifiedClaims ? ' + Clean' : ''} ${item.companyName}`}
                            >
                              {cleaningCompany === item.companyName ? <Loader2 size={14} className="animate-spin" /> : <ShieldAlert size={14} />}
                            </button>
                            <button
                              onClick={() => handleOpenEdit(item.companyName)}
                              disabled={editLoading && editingCompany === item.companyName}
                              className="text-slate-500 hover:text-sky-400 p-1.5 rounded hover:bg-sky-500/10 transition-all disabled:opacity-50"
                              title={`Edit ${item.companyName}`}
                            >
                              {editLoading && editingCompany === item.companyName ? <Loader2 size={14} className="animate-spin" /> : <Pencil size={14} />}
                            </button>
                            <button
                              onClick={() => handleDeleteCompany(item.companyName)}
                              className="text-slate-500 hover:text-rose-400 p-1.5 rounded hover:bg-rose-500/10 transition-all"
                              title={`Delete ${item.companyName}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </div>
                        {/* INLINE EDIT PANEL */}
                        {editingCompany === item.companyName && !editLoading && (
                          <div className="bg-sky-500/5 border border-sky-500/20 rounded-lg p-4 space-y-3">
                            <div className="flex items-center justify-between">
                              <span className="text-sky-300 text-xs font-mono font-bold">EDITING: {item.companyName}</span>
                              <div className="flex items-center gap-2">
                                <button
                                  onClick={handleSaveEdit}
                                  disabled={editSaving}
                                  className="flex items-center gap-1.5 px-3 py-1 bg-emerald-900/40 hover:bg-emerald-800/60 border border-emerald-600/50 text-emerald-200 font-mono font-bold text-[10px] rounded transition-all disabled:opacity-50"
                                >
                                  {editSaving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                                  SAVE
                                </button>
                                <button
                                  onClick={() => setEditingCompany(null)}
                                  className="text-slate-400 hover:text-white p-1"
                                >
                                  <X size={14} />
                                </button>
                              </div>
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-400 font-mono uppercase tracking-wider block mb-1">Summary</label>
                              <textarea
                                value={editData.summary_text}
                                onChange={(e) => setEditData(prev => ({ ...prev, summary_text: e.target.value }))}
                                className="w-full bg-black/40 border border-white/10 rounded-lg p-3 text-slate-200 text-xs font-mono resize-y min-h-[120px] focus:border-sky-500/50 focus:outline-none"
                                rows={8}
                              />
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-400 font-mono uppercase tracking-wider block mb-1">Elevator Pitch</label>
                              <textarea
                                value={editData.pitch_text}
                                onChange={(e) => setEditData(prev => ({ ...prev, pitch_text: e.target.value }))}
                                className="w-full bg-black/40 border border-white/10 rounded-lg p-3 text-slate-200 text-xs font-mono resize-y min-h-[80px] focus:border-sky-500/50 focus:outline-none"
                                rows={4}
                              />
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-400 font-mono uppercase tracking-wider block mb-1">General Summary</label>
                              <textarea
                                value={editData.general_summary}
                                onChange={(e) => setEditData(prev => ({ ...prev, general_summary: e.target.value }))}
                                className="w-full bg-black/40 border border-white/10 rounded-lg p-3 text-slate-200 text-xs font-mono resize-y min-h-[80px] focus:border-sky-500/50 focus:outline-none"
                                rows={4}
                              />
                            </div>
                            <div className="text-[9px] text-slate-600 font-mono">
                              Quick fix — next scan will overwrite these edits.
                            </div>
                          </div>
                        )}
                      </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Login Modal */}
      {isLoginOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-[#0f172a] border border-white/10 rounded-xl shadow-2xl p-6 w-full max-w-sm">
            <div className="flex justify-between items-center mb-5">
              <h3 className="text-sm font-bold text-white">Authentication Required</h3>
              <button onClick={() => { setIsLoginOpen(false); setLoginError(''); }} className="text-slate-400 hover:text-white"><X size={16} /></button>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); const fd = new FormData(e.target as HTMLFormElement); handleLogin(fd.get('password') as string); }}>
              <div className="mb-4">
                <label className="text-[10px] text-slate-400 font-mono uppercase tracking-wider block mb-2">Enter Password</label>
                <input name="password" type="password" autoFocus className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:border-cyan-500/50 focus:outline-none" placeholder="Guest or Admin password" />
              </div>
              {loginError && (
                <div className="mb-3 text-[11px] text-rose-400 bg-rose-900/20 border border-rose-800/30 rounded-lg px-3 py-2">{loginError}</div>
              )}
              <button type="submit" disabled={loginLoading} className="w-full py-2 bg-gradient-to-r from-cyan-600 to-violet-600 hover:from-cyan-500 hover:to-violet-500 text-white text-xs font-bold rounded-lg transition-all disabled:opacity-50">
                {loginLoading ? 'Verifying...' : 'Login'}
              </button>
              <p className="text-[9px] text-slate-500 text-center mt-3">Session expires when you close this tab.</p>
            </form>
          </div>
        </div>
      )}

      <MarketIntelligenceModal
        isOpen={isMarketIntelOpen}
        data={marketIntelData}
        loading={marketIntelLoading}
        onClose={() => setIsMarketIntelOpen(false)}
        onRefresh={isAdmin ? fetchMarketIntelData : undefined}
        onCompanyClick={(company) => { setIsMarketIntelOpen(false); handleCompanyClick(company); }}
        observatoryData={observatoryData}
      />

      <ObservatoryModal
        isOpen={isObservatoryOpen}
        data={observatoryData}
        loading={observatoryLoading}
        onClose={() => setIsObservatoryOpen(false)}
        onRefresh={fetchObservatoryData}
        adminPassword={isAdmin ? authToken : ''}
        marketIntelData={marketIntelData}
      />

      <ThemeExplorer
        isOpen={isThemeExplorerOpen}
        authToken={authToken}
        onClose={() => setIsThemeExplorerOpen(false)}
      />

      <CompanyModal
        company={selectedCompany}
        isOpen={isModalOpen}
        isLoading={isLoadingDetails}
        marketIntelData={marketIntelData}
        observatoryData={observatoryData}
        onClose={() => setIsModalOpen(false)}
        isAdmin={isAdmin}
        authToken={authToken}
        onUpdate={(updated) => {
            setSelectedCompany(updated);
            setData(prev => {
                const idx = prev.findIndex(p => p.companyName === updated.companyName);
                if (idx > -1) {
                    const newArr = [...prev];
                    newArr[idx] = { 
                        ...newArr[idx], 
                        intent_score: updated.intent_score,
                        reality_score: updated.reality_score,
                        reality_gap: updated.reality_gap,
                        lastUpdated: updated.lastUpdated,
                        has_docs: updated.uploaded_docs && updated.uploaded_docs.length > 0
                    }; 
                    return newArr;
                }
                return [...prev, updated];
            });
            fetchSummaryData(true); 
        }}
      />
    </div>
  );
};

(window as any).App = App;
