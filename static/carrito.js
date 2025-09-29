document.addEventListener('DOMContentLoaded', () => {

    // 📞 CONFIGURACIÓN DE WHATSAPP: REEMPLAZA CON TU NÚMERO
    // (Código de país + número, sin el signo '+'. Ejemplo: 521234567890)
    const numeroWhatsApp = "525521908413"; 

    // --- Selectores del DOM ---
    const cartIcon = document.getElementById('cart-icon');
    const cartModal = document.getElementById('cart-modal');
    const closeButton = document.querySelector('.close-button');
    const clearCartBtn = document.querySelector('.clear-cart-btn');
    const productGrid = document.querySelector('.products-grid'); 
    // Selector para el nuevo botón de WhatsApp
    const whatsappBtn = document.getElementById('whatsapp-order-btn'); 

    // --- Funciones de Persistencia (localStorage) ---

    // Función para obtener el carrito de localStorage
    function getCart() {
        const cart = localStorage.getItem('cart');
        const parsedCart = cart ? JSON.parse(cart) : [];
        return parsedCart.map(item => ({
            ...item,
            cantidad: item.cantidad || 1 
        }));
    }

    // Función para guardar el carrito en localStorage
    function saveCart(cart) {
        localStorage.setItem('cart', JSON.stringify(cart));
    }

    // --- Funciones de Actualización de UI ---

    // Función para actualizar el contador del carrito en la UI
    function updateCartCount() {
        const cart = getCart();
        const cartCountElement = document.getElementById('cart-count');
        
        // Sumar la cantidad de todos los productos
        const totalItems = cart.reduce((sum, item) => sum + item.cantidad, 0); 

        if (cartCountElement) {
            cartCountElement.textContent = totalItems;
        }
    }

    // Función para renderizar los productos en el modal del carrito
    function renderCartItems() {
        const cart = getCart();
        const cartItemsList = document.getElementById('cart-items-list');
        const cartTotalElement = document.getElementById('cart-total-price');
        let total = 0;

        // Limpiar la lista antes de renderizar
        cartItemsList.innerHTML = '';

        if (cart.length === 0) {
            cartItemsList.innerHTML = '<li>Tu carrito está vacío.</li>';
        } else {
            cart.forEach(item => {
                // Calcular el subtotal por item
                const itemSubtotal = item.precio * item.cantidad;
                total += itemSubtotal;

                const li = document.createElement('li');
                li.innerHTML = `
                    <span>
                        ${item.nombre} 
                        (${item.cantidad} x $${item.precio.toFixed(2)}) 
                        = $${itemSubtotal.toFixed(2)}
                    </span>
                    <button class="remove-item-btn" data-id="${item.id}" data-all="true">Eliminar</button>
                    `;
                cartItemsList.appendChild(li);
            });
        }
        // Actualizar el total general
        cartTotalElement.textContent = total.toFixed(2);
    }
    
    // -----------------------------------------------------------
    // ⭐ NUEVA FUNCIÓN: GENERAR PEDIDO DE WHATSAPP ⭐
    // -----------------------------------------------------------
    function generarPedidoWhatsApp() {
        const cart = getCart();

        if (cart.length === 0) {
            alert("Tu carrito está vacío. Agrega productos antes de ordenar.");
            return;
        }

        let mensaje = "*¡Hola! Quisiera realizar el siguiente pedido:*\n\n";
        let total = 0;

        // Construir la lista de productos
        cart.forEach((item, index) => {
            const subtotal = item.cantidad * item.precio;
            // Usamos * para negritas en WhatsApp y %0A para salto de línea
            mensaje += `${index + 1}. ${item.nombre} x ${item.cantidad} uds. ($${item.precio.toFixed(2)} c/u)\n`;
            total += subtotal;
        });

        mensaje += `\n*Total Estimado: $${total.toFixed(2)}*\n\n`;
        mensaje += "Por favor, confírmenme la disponibilidad. ¡Gracias!";

        // Codificar el mensaje para que funcione en la URL de WhatsApp
        const mensajeCodificado = encodeURIComponent(mensaje);

        // Crear la URL final y abrir WhatsApp en una nueva pestaña
        const urlWhatsApp = `https://wa.me/${numeroWhatsApp}?text=${mensajeCodificado}`;

        window.open(urlWhatsApp, '_blank');
        
        // Opcional: Cerrar el modal después de enviar el pedido
        cartModal.style.display = 'none';
    }


    // --- Listeners de Eventos ---

    // Delegación de eventos para agregar productos al carrito
    if (productGrid) {
        productGrid.addEventListener('click', (e) => {
            if (e.target.classList.contains('add-to-cart-btn')) {
                const button = e.target;
                const productId = button.getAttribute('data-id');
                const productName = button.getAttribute('data-nombre');
                const productPrice = parseFloat(button.getAttribute('data-precio'));

                let cart = getCart();
                const existingItemIndex = cart.findIndex(item => item.id === productId);

                if (existingItemIndex > -1) {
                    cart[existingItemIndex].cantidad += 1;
                } else {
                    const product = { 
                        id: productId, 
                        nombre: productName, 
                        precio: productPrice,
                        cantidad: 1 
                    };
                    cart.push(product);
                }
                
                saveCart(cart);
                updateCartCount();
                
                // Efecto visual simple de confirmación
                button.textContent = '¡Agregado!';
                button.disabled = true;
                setTimeout(() => {
                    button.textContent = 'Agregar al carrito';
                    button.disabled = false;
                }, 1500);
            }
        });
    }

    // Event listener para abrir el modal del carrito
    if (cartIcon) {
        cartIcon.addEventListener('click', () => {
            renderCartItems(); 
            cartModal.style.display = 'flex';
        });
    }
    
    // ⭐ EVENT LISTENER PARA EL BOTÓN DE WHATSAPP ⭐
    if (whatsappBtn) {
        whatsappBtn.addEventListener('click', generarPedidoWhatsApp);
    }

    // Event listener para cerrar el modal (botón 'x')
    if (closeButton) {
        closeButton.addEventListener('click', () => {
            cartModal.style.display = 'none';
        });
    }

    // Event listener para vaciar el carrito
    if (clearCartBtn) {
        clearCartBtn.addEventListener('click', () => {
            saveCart([]); // Guardar un carrito vacío
            renderCartItems();
            updateCartCount();
        });
    }

    // Delegación de eventos para eliminar un item del carrito
    if (cartModal) {
        cartModal.addEventListener('click', (e) => {
            if (e.target.classList.contains('remove-item-btn')) {
                const productIdToRemove = e.target.getAttribute('data-id');
                let cart = getCart();
                
                // Filtrar (eliminar) todas las instancias del producto con ese ID
                cart = cart.filter(item => item.id !== productIdToRemove);
                
                saveCart(cart);
                renderCartItems();
                updateCartCount();
            }
        });
    }

    // Cerrar el modal al hacer clic fuera de él
    window.addEventListener('click', (e) => {
        if (e.target === cartModal) {
            cartModal.style.display = 'none';
        }
    });

    // Inicializar el contador del carrito al cargar la página
    updateCartCount();
});