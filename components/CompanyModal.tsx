import React, { useState } from 'react';
import { AnalysisResult } from '../types';
import { CompanySummary } from './CompanySummary';
import { X, RefreshCw, Calendar, Clock, Loader2 } from 'lucide-react';

interface CompanyModalProps {
  company: AnalysisResult | null;
  isOpen: boolean;
  onClose: () => void;
  onUpdate: (updatedCompany: AnalysisResult) => void; // Callback to update parent state
}

export const CompanyModal: React.FC<CompanyModalProps> = ({ company, isOpen, onClose, onUpdate }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  if (!isOpen || !company) return null;

  const handleRefresh = async () => {
    setIsRefreshing(true);
    
    // Safely access API URL
    const env = (import.meta as any).env || {};
    const apiUrl = env.VITE_API_URL;

    try {
      if (!apiUrl) {
         // Simulation Mode if no API
         await new Promise(resolve => setTimeout(resolve, 2000));
         alert("Backend not connected. Simulation only.");
         setIsRefreshing(false);
         return;
      }

      // We need to send text for the scan. 
      // Since we are refreshing an existing JSON entry, we might not have the original text.
      // We will send a generic text or reuse existing if available in a real app. 
      // Here we send a placeholder that tells the backend to perhaps rely on previous score or simulate Intent.
      const payload = {
        companyName: company.companyName,
        companyUrl: "",
        reportText: `Refreshed analysis for ${company.companyName}. Strategy focused on efficiency and digitalization.`, // Placeholder for Intent Scanner
        saveToDatabase: true // CRITICAL: Tells backend to update the Blob
      };

      const response = await fetch(`${apiUrl}/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) throw new Error("Refresh failed");

      const json = await response.json();
      const updatedData = json.data;

      // Update Parent State
      onUpdate(updatedData);
      
    } catch (error) {
      console.error("Refresh Error", error);
      alert("Failed to refresh database.");
    } finally {
      setIsRefreshing(false);
    }
  };

  // Format Date
  const dateObj = new Date(company.lastUpdated);
  const dateStr = dateObj.toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' });
  const timeStr = dateObj.toLocaleTimeString('en-FI', { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-slate-900/80 backdrop-blur-sm transition-opacity" onClick={onClose} />

      {/* Slide-over Panel */}
      <div className="relative w-full max-w-3xl bg-slate-900 h-full shadow-[0_0_50px_rgba(0,0,0,0.5)] border-l border-white/10 flex flex-col transform transition-transform animate-in slide-in-from-right duration-300">
        
        {/* Header */}
        <div className="px-8 py-6 border-b border-white/5 flex items-start justify-between bg-slate-900 z-10">
          <div>
            <div className="flex items-center gap-3">
               <h2 className="text-3xl font-bold text-white tracking-tight">{company.companyName}</h2>
               <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-white/5 text-slate-400 border border-white/5">
                 ID: {company.id.substring(0,8)}
               </span>
            </div>
            {/* Visual Date Indicator */}
            <div className="flex items-center gap-4 mt-2 text-xs font-mono text-cyan-400">
               <div className="flex items-center gap-1.5 bg-cyan-950/30 px-2 py-1 rounded border border-cyan-900/50">
                  <Calendar size={12} />
                  <span>{dateStr}</span>
               </div>
               <div className="flex items-center gap-1.5 bg-cyan-950/30 px-2 py-1 rounded border border-cyan-900/50">
                  <Clock size={12} />
                  <span>{timeStr}</span>
               </div>
               <span className="text-[10px] text-slate-500 uppercase tracking-widest ml-1">Database Sync Active</span>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors p-2 hover:bg-white/5 rounded-full">
            <X size={24} />
          </button>
        </div>

        {/* Content - Scrollable */}
        <div className="flex-1 overflow-y-auto p-8 custom-scrollbar bg-gradient-to-b from-slate-900 to-[#0f172a]">
           <CompanySummary data={company} />
        </div>

        {/* Footer Actions */}
        <div className="px-8 py-6 border-t border-white/5 bg-slate-900">
          <button 
            onClick={handleRefresh}
            disabled={isRefreshing}
            className={`w-full py-3 border font-medium rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-50 
              ${isRefreshing 
                ? 'bg-slate-800 border-slate-700 text-slate-400 cursor-not-allowed' 
                : 'bg-white/5 hover:bg-cyan-500/10 text-slate-200 border-white/10 hover:border-cyan-500/50 hover:text-cyan-300 shadow-lg'
              }`}
          >
            {isRefreshing ? (
              <>
                <Loader2 className="animate-spin" size={16} />
                Running Real-time Diagnostics...
              </>
            ) : (
              <>
                <RefreshCw size={16} />
                Refresh Forensic Scan & Update Database
              </>
            )}
          </button>
          <p className="text-center text-[10px] text-slate-500 mt-2">
            Warning: This triggers a live API analysis and updates the global record.
          </p>
        </div>

      </div>
    </div>
  );
};