import React, { useState } from 'react';
import { AnalysisResult } from '../types';
import { CompanySummary } from './CompanySummary';
import { X, RefreshCw } from 'lucide-react';

interface CompanyModalProps {
  company: AnalysisResult | null;
  isOpen: boolean;
  onClose: () => void;
}

export const CompanyModal: React.FC<CompanyModalProps> = ({ company, isOpen, onClose }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  if (!isOpen || !company) return null;

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      alert("Analysis refreshed (Simulation)");
    }, 2000);
  };

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
            className="w-full py-3 bg-white/5 hover:bg-white/10 text-slate-200 border border-white/10 hover:border-white/20 font-medium rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {isRefreshing ? (
              <>
                <div className="w-4 h-4 border-2 border-slate-500 border-t-white rounded-full animate-spin" />
                Updating Analysis...
              </>
            ) : (
              <>
                <RefreshCw size={16} />
                Refresh Forensic Scan
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
};