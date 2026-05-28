module top (
    n0,
    n1,
    n2,
    n3
);

  input [2:0] n0;
  input [3:0] n1;
  input [8:0] n2;
  output n3;

  wire \$or$testcase/test04/test04.v:21$3_Y , \$or$testcase/test04/test04.v:25$8_Y , \$or$testcase/test04/test04.v:26$10_Y , \$or$testcase/test04/test04.v:28$13_Y , \$or$testcase/test04/test04.v:30$16_Y , \$or$testcase/test04/test04.v:31$18_Y , \$or$testcase/test04/test04.v:33$21_Y , \$or$testcase/test04/test04.v:35$24_Y , \$or$testcase/test04/test04.v:37$27_Y , \$or$testcase/test04/test04.v:39$30_Y , \$or$testcase/test04/test04.v:50$44_Y , \$or$testcase/test04/test04.v:51$46_Y , \$or$testcase/test04/test04.v:52$48_Y , \$or$testcase/test04/test04.v:53$50_Y , \$or$testcase/test04/test04.v:63$63_Y , \$or$testcase/test04/test04.v:64$65_Y , \$or$testcase/test04/test04.v:65$67_Y , \$or$testcase/test04/test04.v:68$71_Y , \$or$testcase/test04/test04.v:70$74_Y , \$or$testcase/test04/test04.v:71$76_Y , \$or$testcase/test04/test04.v:73$79_Y , \$xor$testcase/test04/test04.v:43$35_Y , \$xor$testcase/test04/test04.v:44$37_Y , \$xor$testcase/test04/test04.v:57$55_Y , \$xor$testcase/test04/test04.v:60$59_Y , n10, n11, n12, n13, n14, n15, n16, n17, n18, n19, n20, n21, n22, n23, n24, n25, n26, n27, n28, n29, n30, n31, n32, n33, n34, n35, n36, n37, n38, n39, n4, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n5, n50, n51, n52, n53, n54, n55, n56, n57, n58, n59, n6, n60, n61, n62, n63, n64, n65, n66, n67, n68, n7, n8, n9;

  not   \$not$testcase/test04/test04.v:82$89  (n6, n0[1]);
  not   \$not$testcase/test04/test04.v:84$91  (n4, n0[2]);
  not   \$not$testcase/test04/test04.v:78$85  (n10, n1[1]);
  xor   \$xor$testcase/test04/test04.v:60$59  (\$xor$testcase/test04/test04.v:60$59_Y , n0[1], n1[1]);
  not   \$not$testcase/test04/test04.v:77$84  (n11, n1[2]);
  xor   \$xor$testcase/test04/test04.v:57$55  (\$xor$testcase/test04/test04.v:57$55_Y , n0[2], n1[2]);
  not   \$not$testcase/test04/test04.v:76$83  (n12, n1[3]);
  and   \$and$testcase/test04/test04.v:72$78  (n15, n2[0], n0[0]);
  or    \$or$testcase/test04/test04.v:71$76  (\$or$testcase/test04/test04.v:71$76_Y , n2[0], n0[0]);
  not   \$not$testcase/test04/test04.v:79$86  (n9, n2[1]);
  not   \$not$testcase/test04/test04.v:80$87  (n8, n2[2]);
  not   \$not$testcase/test04/test04.v:75$82  (n13, n2[3]);
  xor   \$xor$testcase/test04/test04.v:58$57  (n32, n2[3], n1[3]);
  not   \$not$testcase/test04/test04.v:81$88  (n7, n2[5]);
  not   \$not$testcase/test04/test04.v:83$90  (n5, n2[7]);
  or    \$or$testcase/test04/test04.v:65$67  (\$or$testcase/test04/test04.v:65$67_Y , n6, n2[1]);
  or    \$or$testcase/test04/test04.v:64$65  (\$or$testcase/test04/test04.v:64$65_Y , n4, n2[2]);
  not   \$not$testcase/test04/test04.v:60$60  (n27, \$xor$testcase/test04/test04.v:60$59_Y );
  not   \$not$testcase/test04/test04.v:57$56  (n28, \$xor$testcase/test04/test04.v:57$55_Y );
  or    \$or$testcase/test04/test04.v:50$44  (\$or$testcase/test04/test04.v:50$44_Y , n1[0], n15);
  not   \$not$testcase/test04/test04.v:71$77  (n16, \$or$testcase/test04/test04.v:71$76_Y );
  or    \$or$testcase/test04/test04.v:69$73  (n18, n9, n0[1]);
  or    \$or$testcase/test04/test04.v:62$62  (n24, n8, n0[2]);
  and   \$and$testcase/test04/test04.v:74$81  (n20, n13, n12);
  and   \$and$testcase/test04/test04.v:67$70  (n25, n2[6], n7);
  or    \$or$testcase/test04/test04.v:70$74  (\$or$testcase/test04/test04.v:70$74_Y , n7, n2[6]);
  or    \$or$testcase/test04/test04.v:73$79  (\$or$testcase/test04/test04.v:73$79_Y , n7, n2[4]);
  and   \$and$testcase/test04/test04.v:66$69  (n26, n2[8], n5);
  or    \$or$testcase/test04/test04.v:63$63  (\$or$testcase/test04/test04.v:63$63_Y , n5, n2[6]);
  or    \$or$testcase/test04/test04.v:68$71  (\$or$testcase/test04/test04.v:68$71_Y , n5, n2[8]);
  not   \$not$testcase/test04/test04.v:65$68  (n21, \$or$testcase/test04/test04.v:65$67_Y );
  not   \$not$testcase/test04/test04.v:64$66  (n22, \$or$testcase/test04/test04.v:64$65_Y );
  xor   \$xor$testcase/test04/test04.v:44$37  (\$xor$testcase/test04/test04.v:44$37_Y , n27, n2[1]);
  xor   \$xor$testcase/test04/test04.v:43$35  (\$xor$testcase/test04/test04.v:43$35_Y , n28, n2[2]);
  not   \$not$testcase/test04/test04.v:50$45  (n37, \$or$testcase/test04/test04.v:50$44_Y );
  and   \$and$testcase/test04/test04.v:55$53  (n38, n2[4], n20);
  or    \$or$testcase/test04/test04.v:52$48  (\$or$testcase/test04/test04.v:52$48_Y , n2[4], n20);
  or    \$or$testcase/test04/test04.v:53$50  (\$or$testcase/test04/test04.v:53$50_Y , n2[5], n25);
  not   \$not$testcase/test04/test04.v:70$75  (n17, \$or$testcase/test04/test04.v:70$74_Y );
  not   \$not$testcase/test04/test04.v:73$80  (n14, \$or$testcase/test04/test04.v:73$79_Y );
  or    \$or$testcase/test04/test04.v:51$46  (\$or$testcase/test04/test04.v:51$46_Y , n2[7], n26);
  not   \$not$testcase/test04/test04.v:63$64  (n23, \$or$testcase/test04/test04.v:63$63_Y );
  not   \$not$testcase/test04/test04.v:68$72  (n19, \$or$testcase/test04/test04.v:68$71_Y );
  or    \$or$testcase/test04/test04.v:56$54  (n29, n10, n21);
  or    \$or$testcase/test04/test04.v:54$52  (n33, n11, n22);
  not   \$not$testcase/test04/test04.v:44$38  (n44, \$xor$testcase/test04/test04.v:44$37_Y );
  not   \$not$testcase/test04/test04.v:43$36  (n45, \$xor$testcase/test04/test04.v:43$35_Y );
  not   \$not$testcase/test04/test04.v:52$49  (n35, \$or$testcase/test04/test04.v:52$48_Y );
  not   \$not$testcase/test04/test04.v:53$51  (n34, \$or$testcase/test04/test04.v:53$50_Y );
  or    \$or$testcase/test04/test04.v:61$61  (n30, n25, n14);
  not   \$not$testcase/test04/test04.v:51$47  (n36, \$or$testcase/test04/test04.v:51$46_Y );
  or    \$or$testcase/test04/test04.v:59$58  (n31, n26, n23);
  and   \$and$testcase/test04/test04.v:48$42  (n40, n18, n29);
  and   \$and$testcase/test04/test04.v:45$39  (n43, n24, n33);
  and   \$and$testcase/test04/test04.v:38$29  (n50, n0[0], n44);
  and   \$and$testcase/test04/test04.v:46$40  (n42, n2[4], n34);
  and   \$and$testcase/test04/test04.v:47$41  (n41, n2[6], n36);
  or    \$or$testcase/test04/test04.v:49$43  (n39, n31, n30);
  and   \$and$testcase/test04/test04.v:40$32  (n48, n40, n45);
  or    \$or$testcase/test04/test04.v:37$27  (\$or$testcase/test04/test04.v:37$27_Y , n40, n45);
  and   \$and$testcase/test04/test04.v:42$34  (n46, n32, n43);
  or    \$or$testcase/test04/test04.v:41$33  (n47, n38, n43);
  or    \$or$testcase/test04/test04.v:34$23  (n54, n50, n48);
  or    \$or$testcase/test04/test04.v:36$26  (n52, n44, n48);
  not   \$not$testcase/test04/test04.v:37$28  (n51, \$or$testcase/test04/test04.v:37$27_Y );
  or    \$or$testcase/test04/test04.v:39$30  (\$or$testcase/test04/test04.v:39$30_Y , n32, n47);
  or    \$or$testcase/test04/test04.v:32$20  (n56, n37, n54);
  or    \$or$testcase/test04/test04.v:31$18  (\$or$testcase/test04/test04.v:31$18_Y , n0[0], n52);
  not   \$not$testcase/test04/test04.v:39$31  (n49, \$or$testcase/test04/test04.v:39$30_Y );
  or    \$or$testcase/test04/test04.v:30$16  (\$or$testcase/test04/test04.v:30$16_Y , n16, n56);
  not   \$not$testcase/test04/test04.v:31$19  (n57, \$or$testcase/test04/test04.v:31$18_Y );
  or    \$or$testcase/test04/test04.v:35$24  (\$or$testcase/test04/test04.v:35$24_Y , n35, n49);
  not   \$not$testcase/test04/test04.v:30$17  (n58, \$or$testcase/test04/test04.v:30$16_Y );
  not   \$not$testcase/test04/test04.v:35$25  (n53, \$or$testcase/test04/test04.v:35$24_Y );
  or    \$or$testcase/test04/test04.v:27$12  (n61, n51, n58);
  or    \$or$testcase/test04/test04.v:33$21  (\$or$testcase/test04/test04.v:33$21_Y , n30, n53);
  or    \$or$testcase/test04/test04.v:25$8  (\$or$testcase/test04/test04.v:25$8_Y , n57, n61);
  not   \$not$testcase/test04/test04.v:33$22  (n55, \$or$testcase/test04/test04.v:33$21_Y );
  not   \$not$testcase/test04/test04.v:25$9  (n63, \$or$testcase/test04/test04.v:25$8_Y );
  or    \$or$testcase/test04/test04.v:29$15  (n59, n42, n55);
  or    \$or$testcase/test04/test04.v:23$6  (n65, n38, n63);
  or    \$or$testcase/test04/test04.v:28$13  (\$or$testcase/test04/test04.v:28$13_Y , n17, n59);
  or    \$or$testcase/test04/test04.v:22$5  (n66, n46, n65);
  not   \$not$testcase/test04/test04.v:28$14  (n60, \$or$testcase/test04/test04.v:28$13_Y );
  or    \$or$testcase/test04/test04.v:20$2  (n68, n39, n66);
  or    \$or$testcase/test04/test04.v:26$10  (\$or$testcase/test04/test04.v:26$10_Y , n31, n60);
  not   \$not$testcase/test04/test04.v:26$11  (n62, \$or$testcase/test04/test04.v:26$10_Y );
  or    \$or$testcase/test04/test04.v:24$7  (n64, n41, n62);
  or    \$or$testcase/test04/test04.v:21$3  (\$or$testcase/test04/test04.v:21$3_Y , n19, n64);
  not   \$not$testcase/test04/test04.v:21$4  (n67, \$or$testcase/test04/test04.v:21$3_Y );
  and   \$and$testcase/test04/test04.v:19$1  (n3, n67, n68);

endmodule
