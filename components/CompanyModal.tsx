

const { useState, useRef } = (window as any).React;
const { X, RefreshCw, Calendar, Clock, Loader2: Loader2Icon, UploadCloud, File, Trash2, Zap, AlertCircle, AlertOctagon } = (window as any).Lucide;

const CompanySummary = (window as any).CompanySummary;

const CompanyModal = ({ company, isOpen, onClose, onUpdate, isLoading, marketIntelData, observatoryData = null, isAdmin = false, authToken = '' }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [refreshFiles, setRefreshFiles] = useState([]);
  
  const fileInputRef = useRef(null);

  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

  if (!isOpen || !company) return null;

  const handleFileChange = (e) => {
    const files: any[] = Array.from(e.target.files || []);
    if (files.length + refreshFiles.length > 3) {
        alert("Maximum 3 documents allowed.");
        return;
    }
    
    const validFiles = files.filter((f: any) => {
        const isValidType = f.type === 'application/pdf' || f.type === 'text/html' || f.name.endsWith('.pdf') || f.name.endsWith('.html');
        const isValidSize = f.size <= MAX_FILE_SIZE;
        
        if (!isValidSize) {
            alert(`File "${f.name}" exceeds the 10MB limit.`);
        }
        
        return isValidType && isValidSize;
    });

    setRefreshFiles(prev => [...prev, ...validFiles]);
  };

  const handleRefresh = async () => {
    if (!isAdmin || !authToken) return;

    setIsRefreshing(true);
    try {
      const formData = new FormData();
      formData.append('companyName', company.companyName);
      formData.append('companyUrl', company.companyUrl || "");

      const contextParts = [];
      if (company.summary_text) contextParts.push(`--- PREVIOUS SUMMARY ---\n${company.summary_text}`);
      if (company.pitch_text) contextParts.push(`--- PREVIOUS PITCH ---\n${company.pitch_text}`);
      formData.append('reportText', contextParts.join('\n\n'));

      formData.append('saveToDatabase', 'true');

      refreshFiles.forEach(file => {
          formData.append('files', file);
      });

      const response = await fetch(`/scan`, {
        method: 'POST',
        body: formData,
        headers: { 'Authorization': `Bearer ${authToken}` }
      });

      if (response.status === 401) {
        alert("Session expired. Please log in again.");
        return;
      }
      if (!response.ok) throw new Error("Refresh failed");
      const json = await response.json();
      if (json.data) {
        onUpdate(json.data);
      } else if (json.job_id) {
        let completedResult = null;
        let attempts = 0;
        while (attempts < 240) {
          const statusRes = await fetch(`/scan-status/${json.job_id}?t=${Date.now()}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
          });

          if (statusRes.status === 401) {
            alert("Session expired. Please log in again.");
            return;
          }
          if (!statusRes.ok) throw new Error("Refresh status polling failed");

          const statusJson = await statusRes.json();
          if (statusJson.status === 'completed') {
            completedResult = statusJson.result?.data;
            break;
          }
          if (statusJson.status === 'failed') {
            throw new Error(statusJson.error || "Refresh scan failed");
          }

          await new Promise(resolve => setTimeout(resolve, 2500));
          attempts += 1;
        }

        if (!completedResult) throw new Error("Refresh scan timed out or returned no result");
        onUpdate(completedResult);
      } else {
        throw new Error("Refresh returned an unknown response shape");
      }
      
      // Reset state
      setRefreshFiles([]);
      setShowUpload(false);
      
    } catch (error) {
      console.error("Refresh Error:", error);
      alert("Failed to connect to the backend scanner.");
    } finally {
      setIsRefreshing(false);
    }
  };

  const dateObj = new Date(company.lastUpdated);
  const dateStr = dateObj.toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' });
  const timeStr = dateObj.toLocaleTimeString('en-FI', { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/80 backdrop-blur-sm transition-opacity" onClick={onClose} />
      <div className="relative w-full max-w-3xl bg-slate-900 h-full shadow-[0_0_50px_rgba(0,0,0,0.5)] border-l border-white/10 flex flex-col transform transition-transform animate-in slide-in-from-right duration-300">
        
        {/* HEADER */}
        <div className="px-8 py-6 border-b border-white/5 flex items-start justify-between bg-slate-900 z-10">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
               <h2 className="text-3xl font-bold text-white tracking-tight">{company.companyName}</h2>
               {company.id && <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-white/5 text-slate-400 border border-white/5">
                 ID: {company.id.substring(0,8)}
               </span>}
               {company.has_docs && (
                   <span className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                       <File size={10} /> DEEP DIVE
                   </span>
               )}
               {/* WASHING BADGE IN HEADER */}
               {company.washing_flag && company.intent_score > company.reality_score && (
                   <span className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 shadow-[0_0_10px_rgba(244,63,94,0.3)] animate-pulse">
                       <AlertOctagon size={10} /> {company.washing_type ? company.washing_type.toUpperCase() : "WASHING DETECTED"}
                   </span>
               )}
            </div>
            <div className="flex items-center gap-4 mt-2 text-xs font-mono text-cyan-400">
               <div className="flex items-center gap-1.5 bg-cyan-950/30 px-2 py-1 rounded border border-cyan-900/50">
                  <Calendar size={12} />
                  <span>{dateStr}</span>
               </div>
               <div className="flex items-center gap-1.5 bg-cyan-950/30 px-2 py-1 rounded border border-cyan-900/50">
                  <Clock size={12} />
                  <span>{timeStr}</span>
               </div>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white p-2 hover:bg-white/5 rounded-full">
            <X size={24} />
          </button>
        </div>

        {/* BODY */}
        <div className="flex-1 overflow-y-auto p-8 custom-scrollbar bg-gradient-to-b from-slate-900 to-[#0f172a] space-y-8">
           {(isLoading || company.partial) ? (
               <div className="h-full flex flex-col items-center justify-center space-y-4">
                   <Loader2Icon className="animate-spin text-cyan-400" size={48} />
                   <p className="text-slate-400 text-sm">Loading full forensic report...</p>
               </div>
           ) : (
               <>
                  <CompanySummary data={company} faviData={
                    marketIntelData?.favi_companies?.find((c: any) => c.name === company?.companyName) || null
                  } observatoryData={observatoryData} marketIntelData={marketIntelData} />
               </>
           )}
        </div>

        {/* FOOTER */}
        <div className="px-8 py-6 border-t border-white/5 bg-slate-900 space-y-3">
          
          {/* FILE UPLOAD PANEL */}
          {showUpload && (
              <div className="animate-in slide-in-from-bottom-2 bg-black/30 p-3 rounded-lg border border-white/10 mb-2">
                  <div className="flex justify-between items-center mb-2">
                      <span className="text-[10px] font-bold uppercase text-slate-500">Attach Internal Docs (PDF/HTML)</span>
                      <button onClick={() => setShowUpload(false)}><X size={12} className="text-slate-500 hover:text-white" /></button>
                  </div>
                  <div className="flex gap-2">
                      <div onClick={() => fileInputRef.current?.click()} className="h-10 w-10 flex items-center justify-center bg-white/5 hover:bg-white/10 rounded border border-dashed border-white/20 cursor-pointer text-slate-400 hover:text-cyan-400">
                          <UploadCloud size={16} />
                      </div>
                      <div className="flex-1 flex gap-2 overflow-x-auto custom-scrollbar">
                         {refreshFiles.map((f, i) => (
                             <div key={i} className="flex items-center gap-1 bg-indigo-500/20 border border-indigo-500/30 rounded px-2 text-[10px] text-indigo-200 whitespace-nowrap">
                                 <File size={10} /> {f.name.substring(0, 10)}...
                                 <button onClick={() => setRefreshFiles(p => p.filter((_, idx) => idx !== i))}><X size={10} /></button>
                             </div>
                         ))}
                         {refreshFiles.length === 0 && <span className="text-xs text-slate-600 self-center italic">No files selected</span>}
                      </div>
                  </div>
                  <input type="file" multiple accept=".pdf,.html" ref={fileInputRef} className="hidden" onChange={handleFileChange} />
              </div>
          )}

          {isAdmin && (
          <div className="flex gap-2">
              <button
                onClick={() => setShowUpload(!showUpload)}
                disabled={isRefreshing}
                className={`px-3 py-3 rounded-lg border flex items-center justify-center transition-all ${showUpload ? 'bg-indigo-600 text-white border-indigo-500' : 'bg-white/5 text-slate-400 border-white/10 hover:bg-white/10'}`}
                title="Add Internal Documents"
              >
                 <UploadCloud size={16} />
              </button>

              <button
                onClick={handleRefresh}
                disabled={isRefreshing || isLoading || company.partial}
                className={`flex-1 py-3 border font-medium rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-50
                  ${isRefreshing
                    ? 'bg-slate-800 border-slate-700 text-slate-400 cursor-not-allowed'
                    : 'bg-white/5 hover:bg-cyan-500/10 text-slate-200 border-white/10 hover:border-cyan-500/50 hover:text-cyan-300 shadow-lg'
                  }`}
              >
                {isRefreshing ? (
                  <>
                    <Loader2Icon className="animate-spin" size={16} />
                    Analysing...
                  </>
                ) : (
                  <>
                    <RefreshCw size={16} />
                    {refreshFiles.length > 0 ? "Deep Scan (Docs)" : "Refresh Scan"}
                  </>
                )}
              </button>
          </div>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).CompanyModal = CompanyModal;
