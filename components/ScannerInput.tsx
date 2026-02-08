

const { useState, useRef } = (window as any).React;
const { Search, FileText, Globe, Zap, Link, AlertCircle, Database, Settings, Cpu, UploadCloud, File, X, Sparkles, Loader2 } = (window as any).Lucide;

const ScannerInput = ({ onScan }) => {
  const [companyName, setCompanyName] = useState('');
  const [companyUrl, setCompanyUrl] = useState('');
  const [reportText, setReportText] = useState('');
  const [urlError, setUrlError] = useState('');
  const [showModels, setShowModels] = useState(false);
  const [modelList, setModelList] = useState([]);
  const [loadingModels, setLoadingModels] = useState(false);
  
  // NEW: File Upload State
  const [selectedFiles, setSelectedFiles] = useState([]);
  const fileInputRef = useRef(null);
  
  // NEW: Model Override State
  const [showAdvanced, setShowAdvanced] = useState(false);
  // Default is empty string, which implies "Hybrid Auto-Pilot"
  const [selectedModel, setSelectedModel] = useState('');
  
  // NEW: Auto-Discovery State
  const [isFindingUrl, setIsFindingUrl] = useState(false);

  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

  const validateUrl = (url) => {
    if (!url) return true;
    try {
      new URL(url);
      return true;
    } catch (e) {
      return false;
    }
  };

  const handleUrlChange = (e) => {
    const val = e.target.value;
    setCompanyUrl(val);
    
    if (val && !validateUrl(val)) {
      setUrlError("Invalid URL format. Please include http:// or https://");
    } else {
      setUrlError("");
    }
  };

  const handleNameBlur = async () => {
      // Auto-fetch URL if name exists, URL is empty, and we aren't already searching
      if (companyName.trim().length > 1 && !companyUrl && !isFindingUrl) {
          setIsFindingUrl(true);
          try {
              const res = await fetch(`/api/find-url?name=${encodeURIComponent(companyName)}`);
              if (res.ok) {
                  const json = await res.json();
                  if (json.url && validateUrl(json.url)) {
                      setCompanyUrl(json.url);
                      setUrlError("");
                  }
              }
          } catch (e) {
              console.warn("URL Auto-discovery failed", e);
          } finally {
              setIsFindingUrl(false);
          }
      }
  };

  const handleFileChange = (e) => {
    const files: any[] = Array.from(e.target.files || []);
    if (files.length + selectedFiles.length > 3) {
        alert("Maximum 3 documents allowed.");
        return;
    }
    
    const validFiles = files.filter((f: any) => {
        const isValidType = f.type === 'application/pdf' || f.type === 'text/html' || f.name.endsWith('.pdf') || f.name.endsWith('.html');
        const isValidSize = f.size <= MAX_FILE_SIZE;

        if (!isValidType) {
            console.warn(`Skipped ${f.name}: Invalid type.`);
        }
        if (!isValidSize) {
            alert(`File "${f.name}" exceeds the 10MB limit.`);
        }

        return isValidType && isValidSize;
    });
    
    if (validFiles.length !== files.length && files.some((f: any) => f.size <= MAX_FILE_SIZE)) {
        alert("Some files were skipped due to invalid format.");
    }
    
    setSelectedFiles(prev => [...prev, ...validFiles]);
  };

  const removeFile = (index) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    
    if (!companyName) {
      alert("Company Name is required.");
      return;
    }

    if (companyUrl && !validateUrl(companyUrl)) {
        setUrlError("Invalid URL format. Please include http:// or https://");
        return;
    }

    if (!companyUrl && !reportText && selectedFiles.length === 0) {
      alert("Please provide URL, Text, OR Upload Documents.");
      return;
    }

    // Construct FormData for multipart/form-data submission
    const formData = new FormData();
    formData.append('companyName', companyName);
    if (companyUrl) formData.append('companyUrl', companyUrl);
    formData.append('reportText', reportText);
    
    // Only append modelId if user explicitly selected one. 
    // If empty, backend uses Hybrid default.
    if (selectedModel) {
        formData.append('modelId', selectedModel);
    }
    
    formData.append('saveToDatabase', 'true');
    
    selectedFiles.forEach(file => {
        formData.append('files', file);
    });

    // Pass formData to parent
    onScan(formData, true); // true flag indicates it's FormData
  };

  const fetchModels = async () => {
    if (showModels) {
        setShowModels(false);
        return;
    }
    setLoadingModels(true);
    setShowModels(true);
    try {
        const res = await fetch('/api/models');
        const json = await res.json();
        if (json.models) {
            setModelList(json.models);
        } else {
            setModelList(["Error fetching models: " + JSON.stringify(json)]);
        }
    } catch (e) {
        setModelList(["Network Error: " + e.message]);
    } finally {
        setLoadingModels(false);
    }
  };

  // Helper to display current selection
  const getModelLabel = () => {
      if (!selectedModel) return "STRATEGY: HYBRID (DEFAULT)";
      if (selectedModel.includes('3-pro')) return "STRATEGY: FORCE 3 PRO";
      if (selectedModel.includes('flash')) return "STRATEGY: FORCE 3 FLASH";
      if (selectedModel.includes('2.5')) return "STRATEGY: FORCE 2.5 PRO";
      return selectedModel;
  };

  return (
    <div className="bg-slate-900">
      <div className="bg-white/5 p-6 border-b border-white/5 flex justify-between items-center">
        <div>
            <h3 className="font-bold text-lg text-white flex items-center gap-2 tracking-tight">
            <Zap className="text-cyan-400" size={18} /> Configure Diagnosis
            </h3>
            <p className="text-[10px] text-slate-400 mt-1 uppercase tracking-widest font-semibold">Forensic Parameters (Finland Top 100)</p>
        </div>
        <div className="flex gap-2 text-xs">
           <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className={`px-3 py-1.5 rounded border transition-all font-mono text-[10px] flex items-center gap-1 font-bold ${!selectedModel ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-amber-500/10 text-amber-400 border-amber-500/30'}`}>
            <Cpu size={10} /> {getModelLabel()}
           </button>
           <button type="button" onClick={fetchModels} className="px-3 py-1.5 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-cyan-300 border border-transparent hover:border-cyan-500/30 transition-all font-mono text-[10px] flex items-center gap-1">
            <Database size={10} /> {showModels ? "HIDE LIST" : "CHECK SERVER"}
           </button>
        </div>
      </div>

      {showModels && (
          <div className="bg-black/30 p-4 border-b border-white/5 animate-in slide-in-from-top-2">
            <h4 className="text-xs font-bold text-white mb-2">Available Gemini Models (Server-Side)</h4>
            {loadingModels ? (
                <div className="text-xs text-slate-400">Querying Google Cloud...</div>
            ) : (
                <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto custom-scrollbar">
                    {modelList.map((m, i) => (
                        <div key={i} className="text-[10px] font-mono text-emerald-400 bg-emerald-950/30 px-2 py-1 rounded border border-emerald-900/50 truncate cursor-pointer hover:bg-emerald-900/50" onClick={() => { setSelectedModel(m); setShowModels(false); setShowAdvanced(true); }}>
                            {m}
                        </div>
                    ))}
                    {modelList.length === 0 && <div className="text-xs text-rose-400">No models found or API Key invalid.</div>}
                </div>
            )}
          </div>
      )}

      {showAdvanced && (
          <div className="bg-indigo-950/20 p-4 border-b border-indigo-500/20 animate-in slide-in-from-top-2 flex flex-col gap-2">
             <label className="text-[10px] font-bold uppercase tracking-widest text-indigo-300 flex items-center gap-2">
                <Cpu size={12} /> Model Selection Strategy
             </label>
             <div className="flex flex-col gap-3">
                 <div className="flex gap-1 flex-wrap">
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('')} 
                        className={`flex-1 px-3 py-2 rounded text-[10px] border transition-all flex flex-col items-center gap-1 ${selectedModel === '' ? 'bg-emerald-600 text-white border-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.3)]' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        <span className="font-bold flex items-center gap-1"><Sparkles size={10} /> HYBRID (DEFAULT)</span>
                        <span className="text-[9px] opacity-90">3 Pro (Logic) + 2.5 Pro (Text)</span>
                    </button>
                    
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-3-pro-preview')} 
                        className={`flex-1 px-3 py-2 rounded text-[10px] border transition-all flex flex-col items-center gap-1 ${selectedModel === 'gemini-3-pro-preview' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        <span className="font-bold">3 PRO</span>
                        <span className="text-[9px] opacity-70">Force High Logic</span>
                    </button>
                    
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-3-flash-preview')} 
                        className={`flex-1 px-3 py-2 rounded text-[10px] border transition-all flex flex-col items-center gap-1 ${selectedModel === 'gemini-3-flash-preview' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        <span className="font-bold">3 FLASH</span>
                        <span className="text-[9px] opacity-70">Force Speed</span>
                    </button>
                    
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-2.5-pro')} 
                        className={`flex-1 px-3 py-2 rounded text-[10px] border transition-all flex flex-col items-center gap-1 ${selectedModel === 'gemini-2.5-pro' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        <span className="font-bold">2.5 PRO</span>
                        <span className="text-[9px] opacity-70">Force Creative</span>
                    </button>
                 </div>
                 
                 {selectedModel !== '' && (
                     <div className="flex items-center gap-2 text-[10px] text-amber-300 bg-amber-950/30 p-2 rounded border border-amber-900/50">
                         <AlertCircle size={12} />
                         <span>Override Active: Single model used for entire pipeline.</span>
                     </div>
                 )}
             </div>
          </div>
      )}
      
      <form onSubmit={handleSubmit} className="p-8 space-y-6">
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Target Entity (FI)</label>
            <div className="relative group">
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                onBlur={handleNameBlur}
                placeholder="e.g. Kone, Neste, UPM"
                className="w-full pl-10 pr-4 py-3 bg-black/20 border border-white/10 rounded-lg text-white placeholder-slate-600 focus:ring-1 focus:ring-cyan-500/50 focus:border-cyan-500/50 outline-none transition-all font-medium text-sm"
                required
              />
              <div className="absolute left-3 top-3.5 text-slate-600 group-focus-within:text-cyan-400 transition-colors">
                <Globe size={16} />
              </div>
            </div>
          </div>
          
          <div className="space-y-2">
            <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Domain URL (Recommended)</label>
            <div className="relative group">
                <input
                  type="url"
                  value={companyUrl}
                  onChange={handleUrlChange}
                  placeholder="https://company.fi"
                  className={`w-full pl-10 pr-4 py-3 bg-black/20 border rounded-lg text-white placeholder-slate-600 focus:ring-1 outline-none transition-all font-medium text-sm ${urlError ? 'border-rose-500 focus:ring-rose-500/50' : 'border-white/10 focus:ring-cyan-500/50 focus:border-cyan-500/50'}`}
                />
                <div className={`absolute left-3 top-3.5 transition-colors ${urlError ? 'text-rose-500' : 'text-slate-600 group-focus-within:text-cyan-400'}`}>
                    {isFindingUrl ? (
                        <Loader2 size={16} className="animate-spin text-cyan-400" />
                    ) : (
                        <Link size={16} />
                    )}
                </div>
            </div>
            {urlError && (
              <div className="flex items-center gap-1.5 text-rose-400 mt-1.5 animate-in slide-in-from-left-1 duration-200">
                <AlertCircle size={12} />
                <span className="text-[10px] font-medium">{urlError}</span>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
            <div className="flex justify-between items-center">
                <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Deep Dive Sources (Internal Docs)</label>
                <span className="text-[10px] text-slate-500 bg-white/5 px-2 py-0.5 rounded border border-white/5">Optional • PDF/HTML • Max 3 • &lt;10MB</span>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div 
                    onClick={() => fileInputRef.current?.click()}
                    className="md:col-span-1 h-32 border border-dashed border-white/10 rounded-lg bg-black/20 hover:bg-white/5 hover:border-cyan-500/30 transition-all cursor-pointer flex flex-col items-center justify-center gap-2 group"
                >
                    <UploadCloud size={24} className="text-slate-500 group-hover:text-cyan-400 transition-colors" />
                    <span className="text-xs text-slate-400 font-medium group-hover:text-white">Add Document</span>
                    <input 
                        type="file" 
                        multiple 
                        accept=".pdf,.html,.htm" 
                        ref={fileInputRef} 
                        className="hidden" 
                        onChange={handleFileChange} 
                    />
                </div>

                <div className="md:col-span-2 space-y-2 h-32 overflow-y-auto custom-scrollbar bg-black/10 rounded-lg p-2 border border-white/5">
                    {selectedFiles.length === 0 ? (
                        <div className="h-full flex items-center justify-center text-xs text-slate-600 italic">
                            No documents attached. Analysis will use public data only.
                        </div>
                    ) : (
                        selectedFiles.map((file, idx) => (
                            <div key={idx} className="flex items-center justify-between bg-indigo-500/10 border border-indigo-500/20 px-3 py-2 rounded text-xs text-indigo-200">
                                <div className="flex items-center gap-2 truncate">
                                    <File size={14} />
                                    <span className="truncate max-w-[150px]">{file.name}</span>
                                    <span className="text-[10px] opacity-60">({(file.size / 1024).toFixed(0)}kb)</span>
                                </div>
                                <button type="button" onClick={() => removeFile(idx)} className="hover:text-rose-400"><X size={14} /></button>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>

        <div className="space-y-2">
          <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500 flex justify-between items-center">
            <span>Narrative Input (Additional Context)</span>
            {companyUrl && !urlError && !isFindingUrl && (
                <span className="text-emerald-400 font-normal normal-case tracking-normal flex items-center gap-1">
                    <Zap size={10} /> Auto-Scrape Active
                </span>
            )}
            {isFindingUrl && (
                <span className="text-cyan-400 font-normal normal-case tracking-normal flex items-center gap-1">
                    <Loader2 size={10} className="animate-spin" /> Locating URL...
                </span>
            )}
          </label>
          <div className="relative group">
             <textarea
              value={reportText}
              onChange={(e) => setReportText(e.target.value)}
              placeholder={companyUrl ? "Add any extra context here, or leave empty..." : "Paste Annual Report, Strategy Text, or 'About Us' content here..."}
              className={`w-full pl-10 pr-4 py-3 h-20 bg-black/20 border rounded-lg text-white placeholder-slate-600 focus:ring-1 focus:ring-cyan-500/50 focus:border-cyan-500/50 outline-none transition-all resize-none font-mono text-xs leading-relaxed custom-scrollbar ${companyUrl && !urlError ? 'border-emerald-500/20' : 'border-white/10'}`}
            />
            <div className="absolute left-3 top-3.5 text-slate-600 group-focus-within:text-cyan-400 transition-colors">
              <FileText size={16} />
            </div>
          </div>
        </div>

        <button
          type="submit"
          className="w-full py-4 bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-bold rounded-lg shadow-lg shadow-cyan-900/20 hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center gap-2 group text-sm tracking-wide"
        >
          <Zap size={16} className="text-cyan-200 group-hover:text-white transition-colors" />
          INITIATE DIAGNOSIS
        </button>
      </form>
    </div>
  );
};

(window as any).ScannerInput = ScannerInput;
