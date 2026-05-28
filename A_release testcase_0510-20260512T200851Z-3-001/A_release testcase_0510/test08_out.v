module top (
    n0,
    n1,
    n2,
    n4,
    n3,
    n5,
    n6,
    n7,
    n8,
    n9,
    n10
);

  input [1:0] n0;
  input [1:0] n1;
  input [4:0] n2;
  input [2:0] n4;
  input [4:0] n3;
  input [2:0] n5;
  output [4:0] n6;
  output [4:0] n7;
  output [4:0] n8;
  output n9;
  output n10;

  wire \$or$testcase/test08/test08.v:101$85_Y , \$or$testcase/test08/test08.v:102$87_Y , \$or$testcase/test08/test08.v:103$89_Y , \$or$testcase/test08/test08.v:105$92_Y , \$or$testcase/test08/test08.v:106$94_Y , \$or$testcase/test08/test08.v:109$98_Y , \$or$testcase/test08/test08.v:26$4_Y , \$or$testcase/test08/test08.v:30$10_Y , \$or$testcase/test08/test08.v:44$21_Y , \$or$testcase/test08/test08.v:53$33_Y , \$or$testcase/test08/test08.v:64$41_Y , \$or$testcase/test08/test08.v:73$53_Y , \$or$testcase/test08/test08.v:79$55_Y , \$or$testcase/test08/test08.v:80$57_Y , \$or$testcase/test08/test08.v:81$59_Y , \$or$testcase/test08/test08.v:83$62_Y , \$or$testcase/test08/test08.v:84$64_Y , \$or$testcase/test08/test08.v:85$66_Y , \$or$testcase/test08/test08.v:87$69_Y , \$or$testcase/test08/test08.v:88$71_Y , \$or$testcase/test08/test08.v:91$75_Y , \$or$testcase/test08/test08.v:97$78_Y , \$or$testcase/test08/test08.v:98$80_Y , \$or$testcase/test08/test08.v:99$82_Y , \$xor$testcase/test08/test08.v:28$7_Y , \$xor$testcase/test08/test08.v:46$24_Y , \$xor$testcase/test08/test08.v:47$26_Y , \$xor$testcase/test08/test08.v:60$36_Y , \$xor$testcase/test08/test08.v:66$44_Y , \$xor$testcase/test08/test08.v:67$46_Y , n11, n13, n14, n15, n16, n17, n18, n19, n20, n23, n25, n26, n27, n28, n29, n30, n31, n32, n33, n34, n35, n37, n39, n41, n42, n43, n44, n45, n46, n47, n48, n49, n50, n51, n53, n55, n56, n57, n58, n59, n60, n61, n62, n63, n64, n65, n66, n67, n68, n69, n70, n72, n73, n74, n75, n76, n77, n78, n79, n80, n81, n82, n83, n84, n85, n86, n87, n89;

  not   \$not$testcase/test08/test08.v:22$101  (n89, n0[0]);
  not   \$not$testcase/test08/test08.v:34$103  (n11, n0[0]);
  not   \$not$testcase/test08/test08.v:55$106  (n23, n0[0]);
  not   \$not$testcase/test08/test08.v:75$109  (n39, n0[0]);
  not   \$not$testcase/test08/test08.v:33$102  (n13, n1[0]);
  and   \$and$testcase/test08/test08.v:31$12  (n15, n1[1], n0[1]);
  or    \$or$testcase/test08/test08.v:30$10  (\$or$testcase/test08/test08.v:30$10_Y , n1[1], n0[1]);
  xor   \$xor$testcase/test08/test08.v:28$7  (\$xor$testcase/test08/test08.v:28$7_Y , n1[1], n0[1]);
  not   \$not$testcase/test08/test08.v:93$111  (n57, n2[0]);
  not   \$not$testcase/test08/test08.v:95$113  (n55, n2[3]);
  not   \$not$testcase/test08/test08.v:54$105  (n25, n4[0]);
  and   \$and$testcase/test08/test08.v:48$28  (n31, n4[1], n0[1]);
  or    \$or$testcase/test08/test08.v:53$33  (\$or$testcase/test08/test08.v:53$33_Y , n4[1], n0[1]);
  xor   \$xor$testcase/test08/test08.v:47$26  (\$xor$testcase/test08/test08.v:47$26_Y , n4[1], n0[1]);
  and   \$and$testcase/test08/test08.v:50$30  (n29, n4[2], n0[1]);
  or    \$or$testcase/test08/test08.v:52$32  (n27, n4[2], n0[1]);
  xor   \$xor$testcase/test08/test08.v:46$24  (\$xor$testcase/test08/test08.v:46$24_Y , n4[2], n0[1]);
  not   \$not$testcase/test08/test08.v:111$114  (n74, n3[0]);
  not   \$not$testcase/test08/test08.v:113$116  (n72, n3[3]);
  not   \$not$testcase/test08/test08.v:74$108  (n41, n5[0]);
  and   \$and$testcase/test08/test08.v:68$48  (n47, n5[1], n0[1]);
  or    \$or$testcase/test08/test08.v:73$53  (\$or$testcase/test08/test08.v:73$53_Y , n5[1], n0[1]);
  xor   \$xor$testcase/test08/test08.v:67$46  (\$xor$testcase/test08/test08.v:67$46_Y , n5[1], n0[1]);
  and   \$and$testcase/test08/test08.v:70$50  (n45, n5[2], n0[1]);
  or    \$or$testcase/test08/test08.v:72$52  (n43, n5[2], n0[1]);
  xor   \$xor$testcase/test08/test08.v:66$44  (\$xor$testcase/test08/test08.v:66$44_Y , n5[2], n0[1]);
  or    \$or$testcase/test08/test08.v:29$9  (n17, n1[0], n11);
  or    \$or$testcase/test08/test08.v:49$29  (n30, n4[0], n23);
  or    \$or$testcase/test08/test08.v:69$49  (n46, n5[0], n39);
  or    \$or$testcase/test08/test08.v:32$13  (n14, n13, n0[0]);
  not   \$not$testcase/test08/test08.v:30$11  (n16, \$or$testcase/test08/test08.v:30$10_Y );
  not   \$not$testcase/test08/test08.v:28$8  (n18, \$xor$testcase/test08/test08.v:28$7_Y );
  or    \$or$testcase/test08/test08.v:51$31  (n28, n25, n0[0]);
  not   \$not$testcase/test08/test08.v:53$34  (n26, \$or$testcase/test08/test08.v:53$33_Y );
  not   \$not$testcase/test08/test08.v:47$27  (n32, \$xor$testcase/test08/test08.v:47$26_Y );
  not   \$not$testcase/test08/test08.v:46$25  (n33, \$xor$testcase/test08/test08.v:46$24_Y );
  or    \$or$testcase/test08/test08.v:71$51  (n44, n41, n0[0]);
  not   \$not$testcase/test08/test08.v:73$54  (n42, \$or$testcase/test08/test08.v:73$53_Y );
  not   \$not$testcase/test08/test08.v:67$47  (n48, \$xor$testcase/test08/test08.v:67$46_Y );
  not   \$not$testcase/test08/test08.v:66$45  (n49, \$xor$testcase/test08/test08.v:66$44_Y );
  and   \$and$testcase/test08/test08.v:27$6  (n6[0], n14, n17);
  or    \$or$testcase/test08/test08.v:26$4  (\$or$testcase/test08/test08.v:26$4_Y , n14, n16);
  xor   \$xor$testcase/test08/test08.v:24$2  (n6[1], n14, n18);
  and   \$and$testcase/test08/test08.v:45$23  (n7[0], n28, n30);
  or    \$or$testcase/test08/test08.v:44$21  (\$or$testcase/test08/test08.v:44$21_Y , n28, n26);
  xor   \$xor$testcase/test08/test08.v:42$19  (n7[1], n28, n32);
  and   \$and$testcase/test08/test08.v:65$43  (n8[0], n44, n46);
  or    \$or$testcase/test08/test08.v:64$41  (\$or$testcase/test08/test08.v:64$41_Y , n44, n42);
  xor   \$xor$testcase/test08/test08.v:62$39  (n8[1], n44, n48);
  or    \$or$testcase/test08/test08.v:90$74  (n60, n57, n6[0]);
  not   \$not$testcase/test08/test08.v:26$5  (n19, \$or$testcase/test08/test08.v:26$4_Y );
  or    \$or$testcase/test08/test08.v:108$97  (n77, n74, n7[0]);
  not   \$not$testcase/test08/test08.v:44$22  (n34, \$or$testcase/test08/test08.v:44$21_Y );
  not   \$not$testcase/test08/test08.v:64$42  (n50, \$or$testcase/test08/test08.v:64$41_Y );
  and   \$and$testcase/test08/test08.v:86$68  (n64, n6[1], n60);
  or    \$or$testcase/test08/test08.v:87$69  (\$or$testcase/test08/test08.v:87$69_Y , n6[1], n60);
  or    \$or$testcase/test08/test08.v:25$3  (n20, n15, n19);
  and   \$and$testcase/test08/test08.v:104$91  (n81, n7[1], n77);
  or    \$or$testcase/test08/test08.v:105$92  (\$or$testcase/test08/test08.v:105$92_Y , n7[1], n77);
  or    \$or$testcase/test08/test08.v:43$20  (n35, n31, n34);
  or    \$or$testcase/test08/test08.v:63$40  (n51, n47, n50);
  not   \$not$testcase/test08/test08.v:87$70  (n63, \$or$testcase/test08/test08.v:87$69_Y );
  and   \$and$testcase/test08/test08.v:23$1  (n6[3], n0[1], n20);
  xor   \$xor$testcase/test08/test08.v:38$14  (n6[2], n20, n0[1]);
  not   \$not$testcase/test08/test08.v:105$93  (n80, \$or$testcase/test08/test08.v:105$92_Y );
  and   \$and$testcase/test08/test08.v:41$18  (n37, n27, n35);
  xor   \$xor$testcase/test08/test08.v:40$16  (n73, n35, n33);
  and   \$and$testcase/test08/test08.v:61$38  (n53, n43, n51);
  xor   \$xor$testcase/test08/test08.v:60$36  (\$xor$testcase/test08/test08.v:60$36_Y , n51, n49);
  or    \$or$testcase/test08/test08.v:85$66  (\$or$testcase/test08/test08.v:85$66_Y , n2[1], n63);
  and   \$and$testcase/test08/test08.v:89$73  (n61, n6[3], n55);
  or    \$or$testcase/test08/test08.v:88$71  (\$or$testcase/test08/test08.v:88$71_Y , n55, n6[3]);
  not   \$not$testcase/test08/test08.v:94$112  (n56, n6[2]);
  or    \$or$testcase/test08/test08.v:103$89  (\$or$testcase/test08/test08.v:103$89_Y , n3[1], n80);
  or    \$or$testcase/test08/test08.v:39$15  (n7[3], n29, n37);
  and   \$and$testcase/test08/test08.v:110$100  (n75, n3[2], n73);
  not   \$not$testcase/test08/test08.v:40$17  (n7[2], n73);
  or    \$or$testcase/test08/test08.v:109$98  (\$or$testcase/test08/test08.v:109$98_Y , n73, n3[2]);
  or    \$or$testcase/test08/test08.v:59$35  (n8[3], n45, n53);
  not   \$not$testcase/test08/test08.v:60$37  (n8[2], \$xor$testcase/test08/test08.v:60$36_Y );
  not   \$not$testcase/test08/test08.v:85$67  (n65, \$or$testcase/test08/test08.v:85$66_Y );
  not   \$not$testcase/test08/test08.v:88$72  (n62, \$or$testcase/test08/test08.v:88$71_Y );
  and   \$and$testcase/test08/test08.v:92$77  (n58, n2[2], n56);
  or    \$or$testcase/test08/test08.v:91$75  (\$or$testcase/test08/test08.v:91$75_Y , n56, n2[2]);
  not   \$not$testcase/test08/test08.v:103$90  (n82, \$or$testcase/test08/test08.v:103$89_Y );
  and   \$and$testcase/test08/test08.v:107$96  (n78, n7[3], n72);
  or    \$or$testcase/test08/test08.v:106$94  (\$or$testcase/test08/test08.v:106$94_Y , n72, n7[3]);
  not   \$not$testcase/test08/test08.v:109$99  (n76, \$or$testcase/test08/test08.v:109$98_Y );
  or    \$or$testcase/test08/test08.v:84$64  (\$or$testcase/test08/test08.v:84$64_Y , n64, n65);
  not   \$not$testcase/test08/test08.v:91$76  (n59, \$or$testcase/test08/test08.v:91$75_Y );
  or    \$or$testcase/test08/test08.v:102$87  (\$or$testcase/test08/test08.v:102$87_Y , n81, n82);
  not   \$not$testcase/test08/test08.v:106$95  (n79, \$or$testcase/test08/test08.v:106$94_Y );
  not   \$not$testcase/test08/test08.v:84$65  (n66, \$or$testcase/test08/test08.v:84$64_Y );
  not   \$not$testcase/test08/test08.v:102$88  (n83, \$or$testcase/test08/test08.v:102$87_Y );
  or    \$or$testcase/test08/test08.v:83$62  (\$or$testcase/test08/test08.v:83$62_Y , n58, n66);
  or    \$or$testcase/test08/test08.v:101$85  (\$or$testcase/test08/test08.v:101$85_Y , n75, n83);
  not   \$not$testcase/test08/test08.v:83$63  (n67, \$or$testcase/test08/test08.v:83$62_Y );
  not   \$not$testcase/test08/test08.v:101$86  (n84, \$or$testcase/test08/test08.v:101$85_Y );
  or    \$or$testcase/test08/test08.v:82$61  (n68, n59, n67);
  or    \$or$testcase/test08/test08.v:100$84  (n85, n76, n84);
  or    \$or$testcase/test08/test08.v:81$59  (\$or$testcase/test08/test08.v:81$59_Y , n61, n68);
  or    \$or$testcase/test08/test08.v:99$82  (\$or$testcase/test08/test08.v:99$82_Y , n78, n85);
  not   \$not$testcase/test08/test08.v:81$60  (n69, \$or$testcase/test08/test08.v:81$59_Y );
  not   \$not$testcase/test08/test08.v:99$83  (n86, \$or$testcase/test08/test08.v:99$82_Y );
  or    \$or$testcase/test08/test08.v:80$57  (\$or$testcase/test08/test08.v:80$57_Y , n62, n69);
  or    \$or$testcase/test08/test08.v:98$80  (\$or$testcase/test08/test08.v:98$80_Y , n79, n86);
  not   \$not$testcase/test08/test08.v:80$58  (n70, \$or$testcase/test08/test08.v:80$57_Y );
  not   \$not$testcase/test08/test08.v:98$81  (n87, \$or$testcase/test08/test08.v:98$80_Y );
  or    \$or$testcase/test08/test08.v:79$55  (\$or$testcase/test08/test08.v:79$55_Y , n70, n2[4]);
  or    \$or$testcase/test08/test08.v:97$78  (\$or$testcase/test08/test08.v:97$78_Y , n87, n3[4]);
  not   \$not$testcase/test08/test08.v:79$56  (n9, \$or$testcase/test08/test08.v:79$55_Y );
  not   \$not$testcase/test08/test08.v:97$79  (n10, \$or$testcase/test08/test08.v:97$78_Y );

  assign n6[4] = 1'bx;
  assign n7[4] = 1'bx;
  assign n8[4] = 1'bx;

endmodule
