
const { useState } = (window as any).React;
const { X, RefreshCw, Calendar, Clock, Loader2: Loader2Icon } = (window as any).Lucide;

const CompanySummary = (window as any).CompanySummary;

const CompanyModal = ({ company, isOpen, onClose, onUpdate, isLoading }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  if (!isOpen || !company) return null;

  const handleRefresh = async () => {
    setIsRefreshing(true);
    const apiUrl = ""; 
    try {
      const payload = {
        companyName: company.companyName,
        companyUrl: company.companyUrl || "", 
        reportText: "", 
        saveToDatabase: true
      };

      const response = await fetch(`${apiUrl}/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) throw new Error("Refresh failed");
      const json = await response.json();
      onUpdate(json.data);
      
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
            <div className="flex items-center gap-3">
               <h2 className="text-3xl font-bold text-white tracking-tight">{company.companyName}</h2>
               {company.id && <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-white/5 text-slate-400 border border-white/5">
                 ID: {company.id.substring(0,8)}
               </span>}
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
        <div className="flex-1 overflow-y-auto p-8 custom-scrollbar bg-gradient-to-b from-slate-900 to-[#0f172a]">
           {(isLoading || company.partial) ? (
               <div className="h-full flex flex-col items-center justify-center space-y-4">
                   <Loader2Icon className="animate-spin text-cyan-400" size={48} />
                   <p className="text-slate-400 text-sm">Loading full forensic report...</p>
               </div>
           ) : (
               <CompanySummary data={company} />
           )}
        </div>

        {/* FOOTER */}
        <div className="px-8 py-6 border-t border-white/5 bg-slate-900">
          <button 
            onClick={handleRefresh}
            disabled={isRefreshing || isLoading || company.partial}
            className={`w-full py-3 border font-medium rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-50 
              ${isRefreshing 
                ? 'bg-slate-800 border-slate-700 text-slate-400 cursor-not-allowed' 
                : 'bg-white/5 hover:bg-cyan-500/10 text-slate-200 border-white/10 hover:border-cyan-500/50 hover:text-cyan-300 shadow-lg'
              }`}
          >
            {isRefreshing ? (
              <>
                <Loader2Icon className="animate-spin" size={16} />
                Running Real-time Diagnostics...
              </>
            ) : (
              <>
                <RefreshCw size={16} />
                Refresh Forensic Scan & Update Database
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

(window as any).CompanyModal = CompanyModal;
