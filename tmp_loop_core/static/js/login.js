// Environment detection: localhost → local server, otherwise → production
const _isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
const API_BASE = _isLocal ? '' : 'https://mlbackend.net/loopcore';

// Get stored token
function getToken() {
    return localStorage.getItem('loopcore_token');
}

// Check if already logged in
async function checkAuth() {
    const token = getToken();
    if (!token) return;

    try {
        const response = await fetch(API_BASE + '/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
            const user = await response.json();
            // Redirect based on role
            if (user.role === 'platform_admin') {
                window.location.href = _isLocal ? '/static/index.html' : 'index.html';
            } else {
                window.location.href = _isLocal ? '/static/customer.html' : 'customer.html';
            }
        } else {
            // Token invalid, clear it
            localStorage.removeItem('loopcore_token');
            localStorage.removeItem('loopcore_user');
        }
    } catch (e) {
        // Not logged in, stay on login page
    }
}

async function handleLogin(event) {
    event.preventDefault();

    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const errorDiv = document.getElementById('error-message');
    const btnText = document.getElementById('btn-text');
    const btnLoading = document.getElementById('btn-loading');
    const loginBtn = document.getElementById('login-btn');

    // Reset error
    errorDiv.classList.remove('show');
    errorDiv.textContent = '';

    // Show loading
    btnText.textContent = 'Signing in...';
    btnLoading.style.display = 'inline-block';
    loginBtn.disabled = true;

    try {
        const response = await fetch(API_BASE + '/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || data.error || 'Login failed');
        }

        // Store token and user info in localStorage
        localStorage.setItem('loopcore_token', data.token);
        localStorage.setItem('loopcore_user', JSON.stringify(data.user));

        // Login successful - redirect based on role
        if (data.user.role === 'platform_admin') {
            window.location.href = _isLocal ? '/static/index.html' : 'index.html';
        } else {
            window.location.href = _isLocal ? '/static/customer.html' : 'customer.html';
        }

    } catch (error) {
        errorDiv.textContent = error.message;
        errorDiv.classList.add('show');
    } finally {
        btnText.textContent = 'Sign In';
        btnLoading.style.display = 'none';
        loginBtn.disabled = false;
    }
}

// Check auth on page load
document.addEventListener('DOMContentLoaded', checkAuth);
