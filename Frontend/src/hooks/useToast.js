import { useState } from 'react';

export function useToast() {
  const [message, setMessage] = useState(null);
  const toast = (msg) => setMessage(msg);
  const clearToast = () => setMessage(null);
  return { message, toast, clearToast };
}
