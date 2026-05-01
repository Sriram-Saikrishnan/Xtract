import { useState } from 'react';
import { useAuth } from './context/AuthContext';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import Toast from './components/Toast';
import Dashboard from './pages/Dashboard';
import Upload from './pages/Upload';
import Processing from './pages/Processing';
import Extractions from './pages/Extractions';
import Detail from './pages/Detail';
import Profile from './pages/Profile';
import Login from './pages/Login';
import Signup from './pages/Signup';

export default function App() {
  const { user } = useAuth();
  const [page, setPage] = useState('dashboard');
  const [jobId, setJobId] = useState(null);
  const [invoiceId, setInvoiceId] = useState(null);
  const [uploadedFiles, setUploadedFiles] = useState(null);
  const [toastMsg, setToastMsg] = useState(null);
  const [authPage, setAuthPage] = useState('login');
  const toast = msg => setToastMsg(msg);

  const navigate = (newPage, opts = {}) => {
    if (opts.jobId !== undefined) setJobId(opts.jobId);
    if (opts.invoiceId !== undefined) setInvoiceId(opts.invoiceId);
    if (opts.uploadedFiles !== undefined) setUploadedFiles(opts.uploadedFiles);
    setPage(newPage);
  };

  if (!user) {
    return (
      <>
        {authPage === 'login'
          ? <Login onSwitch={() => setAuthPage('signup')} toast={toast} />
          : <Signup onSwitch={() => setAuthPage('login')} toast={toast} />}
        <Toast message={toastMsg} onClose={() => setToastMsg(null)} />
      </>
    );
  }

  const pageProps = { navigate, toast };

  let Content;
  switch (page) {
    case 'upload':      Content = <Upload {...pageProps} />; break;
    case 'processing':  Content = <Processing {...pageProps} jobId={jobId} uploadedFiles={uploadedFiles} />; break;
    case 'extractions': Content = <Extractions {...pageProps} highlightJobId={jobId} />; break;
    case 'detail':      Content = <Detail {...pageProps} invoiceId={invoiceId} jobId={jobId} />; break;
    case 'profile':     Content = <Profile {...pageProps} />; break;
    default:            Content = <Dashboard {...pageProps} />;
  }

  return (
    <div className="app">
      <Sidebar page={page} navigate={navigate} />
      <div>
        <Topbar />
        {Content}
      </div>
      <Toast message={toastMsg} onClose={() => setToastMsg(null)} />
    </div>
  );
}
