


const { useState, useRef } = (window as any).React;
const { Search, FileText, Globe, Zap, Link, AlertCircle, Database, Settings, Cpu, UploadCloud, File, X, Loader2 } = (window as any).Lucide;

const ScannerInput = ({ onScan }) => {
  const [companyName, setCompanyName] = useState('');
  const [companyUrl, setCompanyUrl] = useState('');
  const [reportText, setReportText] = useState('');
  const [urlError, setUrlError] = useState('');

  // NEW: File Upload State
  const [selectedFiles, setSelectedFiles] = useState([]);
  const fileInputRef = useRef(null);

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
    formData.append('saveToDatabase', 'true');

    selectedFiles.forEach(file => {
        formData.append('files', file);
    });

    // Pass formData to parent
    onScan(formData, true); // true flag indicates it's FormData
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
      </div>

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
